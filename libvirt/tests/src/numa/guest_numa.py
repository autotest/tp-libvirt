import re
import logging

from avocado.utils import process

from virttest import virt_vm
from virttest import libvirt_xml
from virttest import utils_misc
from virttest import utils_config
from virttest import utils_libvirtd
from virttest import test_setup
from virttest.utils_test import libvirt as utlv

from provider import libvirt_version


def handle_param(param_tuple, params):
    """
    Return param dict list

    :param param_tuple: tuple of key value string which contains '_'
    :return: list of dict from params
    """
    param_list = []
    param_dict = {}
    param_key = []
    param_key_all = []
    key_len = 0
    for param in param_tuple:
        for key in params.keys():
            if param in key:
                param_key.append(key)
        param_key.sort()
        param_key_all.append(param_key)
        key_len = len(param_key)
        param_key = []
    for i in range(key_len):
        for j in range(len(param_tuple)):
            param_dict[param_tuple[j].split('_')[1]] = params.get(
                param_key_all[j][i])
        param_list.append(param_dict.copy())
        param_dict = {}
    logging.debug("param list is %s", param_list)
    return param_list


def run(test, params, env):
    """
    Test guest numa setting
    """
    vcpu_num = int(params.get("vcpu_num", 2))
    max_mem = int(params.get("max_mem", 1048576))
    max_mem_unit = params.get("max_mem_unit", 'KiB')
    vcpu_placement = params.get("vcpu_placement", 'static')
    bug_url = params.get("bug_url", "")
    status_error = "yes" == params.get("status_error", "no")
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    backup_xml = libvirt_xml.VMXML.new_from_dumpxml(vm_name)
    mode_dict = {'strict': 'bind', 'preferred': 'prefer',
                 'interleave': 'interleave'}

    # Prepare numatune memory parameter dict and list
    mem_tuple = ('memory_mode', 'memory_placement', 'memory_nodeset')
    numa_memory = {}
    for mem_param in mem_tuple:
        value = params.get(mem_param)
        if value:
            numa_memory[mem_param.split('_')[1]] = value

    memnode_tuple = ('memnode_cellid', 'memnode_mode', 'memnode_nodeset')
    numa_memnode = handle_param(memnode_tuple, params)

    if numa_memnode:
        if not libvirt_version.version_compare(1, 2, 7):
            test.cancel("Setting hugepages more specifically per "
                        "numa node not supported on current "
                        "version")

    # Prepare cpu numa cell parameter
    topology = {}
    topo_tuple = ('sockets', 'cores', 'threads')
    for key in topo_tuple:
        if params.get(key):
            topology[key] = params.get(key)

    cell_tuple = ('cell_id', 'cell_cpus', 'cell_memory')
    numa_cell = handle_param(cell_tuple, params)

    # Prepare qemu cmdline check parameter
    cmdline_tuple = ("qemu_cmdline",)
    cmdline_list = handle_param(cmdline_tuple, params)

    # Prepare hugepages parameter
    backup_list = []
    page_tuple = ('vmpage_size', 'vmpage_unit', 'vmpage_nodeset')
    page_list = handle_param(page_tuple, params)
    nr_pagesize_total = params.get("nr_pagesize_total")
    deallocate = False
    default_nr_hugepages_path = "/sys/kernel/mm/hugepages/hugepages-2048kB/"
    default_nr_hugepages_path += "nr_hugepages"

    if page_list:
        if not libvirt_version.version_compare(1, 2, 5):
            test.cancel("Setting hugepages more specifically per "
                        "numa node not supported on current "
                        "version")

    hp_cl = test_setup.HugePageConfig(params)
    default_hp_size = hp_cl.get_hugepage_size()
    supported_hp_size = hp_cl.get_multi_supported_hugepage_size()
    mount_path = []
    qemu_conf = utils_config.LibvirtQemuConfig()
    libvirtd = utils_libvirtd.Libvirtd()
    qemu_conf_restore = False

    def _update_qemu_conf():
        """
        Mount hugepage path, update qemu conf then restart libvirtd
        """
        size_dict = {'2048': '2M', '1048576': '1G', '16384': '16M'}
        for page in page_list:
            if page['size'] not in supported_hp_size:
                test.cancel("Hugepage size [%s] isn't supported, "
                            "please verify kernel cmdline configuration."
                            % page['size'])
            m_path = "/dev/hugepages%s" % size_dict[page['size']]
            hp_cl.hugepage_size = int(page['size'])
            hp_cl.hugepage_path = m_path
            hp_cl.mount_hugepage_fs()
            mount_path.append(m_path)
        if mount_path:
            qemu_conf.hugetlbfs_mount = mount_path
            libvirtd.restart()

    try:
        # Get host numa node list
        host_numa_node = utils_misc.NumaInfo()
        node_list = host_numa_node.online_nodes
        logging.debug("host node list is %s", node_list)
        used_node = []
        if numa_memory.get('nodeset'):
            used_node += utlv.cpus_parser(numa_memory['nodeset'])
        if numa_memnode:
            for i in numa_memnode:
                used_node += utlv.cpus_parser(i['nodeset'])
        if page_list:
            host_page_tuple = ("hugepage_size", "page_num", "page_nodenum")
            h_list = handle_param(host_page_tuple, params)
            h_nodenum = [h_list[p_size]['nodenum']
                         for p_size in range(len(h_list))]
            for i in h_nodenum:
                used_node += utlv.cpus_parser(i)
        if used_node and not status_error:
            logging.debug("set node list is %s", used_node)
            used_node = list(set(used_node))
            for i in used_node:
                if i not in node_list:
                    test.cancel("%s in nodeset out of range" % i)
                mem_size = host_numa_node.read_from_node_meminfo(i, 'MemTotal')
                logging.debug("the memory total in the node %s is %s", i, mem_size)
                if not int(mem_size):
                    test.cancel("node %s memory is empty" % i)

        # set hugepage with qemu.conf and mount path
        if default_hp_size == 2048:
            hp_cl.setup()
            deallocate = True
        else:
            _update_qemu_conf()
            qemu_conf_restore = True

        # set hugepage with total number or per-node number
        if nr_pagesize_total:
            # Only set total 2M size huge page number as total 1G size runtime
            # update not supported now.
            deallocate = True
            hp_cl.kernel_hp_file = default_nr_hugepages_path
            hp_cl.target_hugepages = int(nr_pagesize_total)
            hp_cl.set_hugepages()
        if page_list:
            hp_size = [h_list[p_size]['size'] for p_size in range(len(h_list))]
            multi_hp_size = hp_cl.get_multi_supported_hugepage_size()
            for size in hp_size:
                if size not in multi_hp_size:
                    test.cancel("The hugepage size %s not "
                                "supported or not configured under"
                                " current running kernel." % size)
            # backup node page setting and set new value
            for i in h_list:
                node_val = hp_cl.get_node_num_huge_pages(i['nodenum'],
                                                         i['size'])
                # set hugpege per node if current value not satisfied
                # kernel 1G hugepage runtime number update is supported now
                if int(i['num']) > node_val:
                    node_dict = i.copy()
                    node_dict['num'] = node_val
                    backup_list.append(node_dict)
                    hp_cl.set_node_num_huge_pages(i['num'],
                                                  i['nodenum'],
                                                  i['size'])
                    node_val_after_set = hp_cl.get_node_num_huge_pages(i['nodenum'],
                                                                       i['size'])
                    if node_val_after_set < int(i['num']):
                        test.cancel("There is not enough memory to allocate.")

        vmxml = libvirt_xml.VMXML.new_from_dumpxml(vm_name)
        vmxml.vcpu = vcpu_num
        vmxml.max_mem = max_mem
        vmxml.max_mem_unit = max_mem_unit
        vmxml.current_mem = max_mem
        vmxml.current_mem_unit = max_mem_unit

        # numatune setting
        if numa_memnode:
            vmxml.numa_memory = numa_memory
            vmxml.numa_memnode = numa_memnode
            del vmxml.numa_memory
        if numa_memory:
            vmxml.numa_memory = numa_memory

        # vcpu placement setting
        vmxml.placement = vcpu_placement

        # guest numa cpu setting
        vmcpuxml = libvirt_xml.vm_xml.VMCPUXML()
        vmcpuxml.xml = "<cpu><numa/></cpu>"
        if topology:
            vmcpuxml.topology = topology
        logging.debug(vmcpuxml.numa_cell)
        vmcpuxml.numa_cell = numa_cell
        logging.debug(vmcpuxml.numa_cell)
        vmxml.cpu = vmcpuxml

        # hugepages setting
        if page_list:
            membacking = libvirt_xml.vm_xml.VMMemBackingXML()
            hugepages = libvirt_xml.vm_xml.VMHugepagesXML()
            pagexml_list = []
            for i in range(len(page_list)):
                pagexml = hugepages.PageXML()
                pagexml.update(page_list[i])
                pagexml_list.append(pagexml)
            hugepages.pages = pagexml_list
            membacking.hugepages = hugepages
            vmxml.mb = membacking

        logging.debug("vm xml is %s", vmxml)
        vmxml.sync()

        try:
            vm.start()
            session = vm.wait_for_login()
            vmxml_new = libvirt_xml.VMXML.new_from_dumpxml(vm_name)
            logging.debug("vm xml after start is %s", vmxml_new)

        except virt_vm.VMStartError, e:
            # Starting VM failed.
            if status_error:
                return
            else:
                test.fail("Test failed in positive case.\n error:"
                          " %s\n%s" % (e, bug_url))

        vm_pid = vm.get_pid()
        # numa hugepage check
        if page_list:
            numa_maps = open("/proc/%s/numa_maps" % vm_pid)
            numa_map_info = numa_maps.read()
            numa_maps.close()
            hugepage_info = re.findall(".*file=\S*hugepages.*", numa_map_info)
            if not hugepage_info:
                test.fail("Can't find hugepages usage info in vm "
                          "numa maps")
            else:
                logging.debug("The hugepage info in numa_maps is %s" %
                              hugepage_info)
                map_dict = {}
                usage_dict = {}
                node_pattern = r"\s(\S+):(\S+)\s.*ram-node(\d+).*\s"
                node_pattern += "N(\d+)=(\d+)"
                for map_info in hugepage_info:
                    for (mem_mode, mem_num, cell_num, host_node_num,
                         vm_page_num) in re.findall(node_pattern, map_info):
                        usage_dict[mem_mode] = utlv.cpus_parser(mem_num)
                        usage_dict[host_node_num] = vm_page_num
                        map_dict[cell_num] = usage_dict.copy()
                logging.debug("huagepage info in vm numa maps is %s",
                              map_dict)
                memnode_dict = {}
                usage_dict = {}
                if numa_memnode:
                    for i in numa_memnode:
                        node = utlv.cpus_parser(i['nodeset'])
                        mode = mode_dict[i['mode']]
                        usage_dict[mode] = node
                        memnode_dict[i['cellid']] = usage_dict.copy()
                    logging.debug("memnode setting dict is %s", memnode_dict)
                    for k in memnode_dict.keys():
                        for mk in memnode_dict[k].keys():
                            if memnode_dict[k][mk] != map_dict[k][mk]:
                                test.fail("vm pid numa map dict %s"
                                          " not expected" % map_dict)

        # qemu command line check
        f_cmdline = open("/proc/%s/cmdline" % vm_pid)
        q_cmdline_list = f_cmdline.read().split("\x00")
        f_cmdline.close()
        logging.debug("vm qemu cmdline list is %s" % q_cmdline_list)
        for cmd in cmdline_list:
            logging.debug("checking '%s' in qemu cmdline", cmd['cmdline'])
            p_found = False
            for q_cmd in q_cmdline_list:
                if re.search(cmd['cmdline'], q_cmd):
                    p_found = True
                    break
                else:
                    continue
            if not p_found:
                test.fail("%s not found in vm qemu cmdline" % cmd['cmdline'])

        # vm inside check
        vm_cpu_info = utils_misc.get_cpu_info(session)
        logging.debug("lscpu output dict in vm is %s", vm_cpu_info)
        session.close()
        node_num = int(vm_cpu_info["NUMA node(s)"])
        if node_num != len(numa_cell):
            test.fail("node number %s in vm is not expected" % node_num)
        for i in range(len(numa_cell)):
            cpu_str = vm_cpu_info["NUMA node%s CPU(s)" % i]
            vm_cpu_list = utlv.cpus_parser(cpu_str)
            cpu_list = utlv.cpus_parser(numa_cell[i]["cpus"])
            if vm_cpu_list != cpu_list:
                test.fail("vm node %s cpu list %s not expected"
                          % (i, vm_cpu_list))
        if topology:
            vm_topo_tuple = ("Socket(s)", "Core(s) per socket",
                             "Thread(s) per core")
            for i in range(len(topo_tuple)):
                topo_info = vm_cpu_info[vm_topo_tuple[i]]
                if topo_info != topology[topo_tuple[i]]:
                    test.fail("%s in vm topology not expected." % topo_tuple[i])
    finally:
        if vm.is_alive():
            vm.destroy(gracefully=False)
        backup_xml.sync()
        if page_list:
            for i in backup_list:
                hp_cl.set_node_num_huge_pages(i['num'],
                                              i['nodenum'], i['size'])
        if deallocate:
            hp_cl.deallocate = deallocate
            hp_cl.cleanup()
        if qemu_conf_restore:
            qemu_conf.restore()
            libvirtd.restart()
            for mt_path in mount_path:
                try:
                    process.run("umount %s" % mt_path, shell=True)
                except process.CmdError:
                    logging.warning("umount %s failed" % mt_path)
