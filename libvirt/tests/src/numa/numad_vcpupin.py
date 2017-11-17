import logging

from autotest.client import utils
from autotest.client.shared import error

from virttest import libvirt_xml
from virttest import virsh
from virttest import utils_misc
from virttest import utils_test
from virttest import utils_libvirtd


def run(test, params, env):
    """
    Test vcpupin while numad is running
    """

    def cpu_adapter_mod(par_list, std_list):
        if not set(par_list).issubset(std_list):
            if not len(par_list) > len(std_list):
                tmp_list = []
                for un in par_list:
                    if un in std_list:
                        tmp_list.append(un)
                for nd in std_list:
                    if not len(par_list) > len(tmp_list):
                        break
                    if nd not in tmp_list:
                        tmp_list.append(nd)
                if len(par_list) == len(tmp_list):
                    par_list = tmp_list
        return par_list

    vcpu_placement = params.get("vcpu_placement")
    cpu_adapter = "yes" == params.get("cpu_adapter", "no")
    bug_url = params.get("bug_url", "")
    status_error = "yes" == params.get("status_error", "no")
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    backup_xml = libvirt_xml.VMXML.new_from_dumpxml(vm_name)

    # Prepare numatune memory parameter dict
    mem_tuple = ('memory_mode', 'memory_placement', 'memory_nodeset')
    numa_memory = {}
    for mem_param in mem_tuple:
        value = params.get(mem_param)
        if value:
            numa_memory[mem_param.split('_')[1]] = value

    libvirtd = utils_libvirtd.Libvirtd()
    libvirtd.start()

    try:
        # Get host numa node list
        host_numa_node = utils_misc.NumaInfo()
        node_list = host_numa_node.online_nodes_withmem
        logging.debug("host node list is %s", node_list)
        if numa_memory.get('nodeset'):
            used_node = utils_test.libvirt.cpus_parser(numa_memory['nodeset'])
            if cpu_adapter:
                used_node = cpu_adapter_mod(used_node, node_list)
            logging.debug("set node list is %s", used_node)
            if not status_error:
                if not set(used_node).issubset(node_list):
                    raise test.cancel("nodeset %s out of range" %
                                      numa_memory['nodeset'])
        # Start numad
        try:
            utils.run("service numad start")
        except error.CmdError, e:
            # Bug 1218149 closed as not a bug, workaround this as in bug
            # comment 12
            logging.debug("start numad failed with %s", e)
            logging.debug("remove message queue of id 0 and try again")
            utils.run("ipcrm msg 0", ignore_status=True)
            utils.run("service numad start")

        # Start vm and do vcpupin
        vmxml = libvirt_xml.VMXML.new_from_dumpxml(vm_name)
        vmxml.numa_memory = numa_memory
        vmxml.placement = vcpu_placement
        logging.debug("vm xml is %s", vmxml)
        vmxml.sync()
        vm.start()
        vm.wait_for_login()

        # Test vcpupin to the alive cpus list
        cpus_list = utils.cpu_online_map()
        logging.info("active cpus in host are %s", cpus_list)
        for cpu in cpus_list:
            ret = virsh.vcpupin(vm_name, 0, cpu, debug=True,
                                ignore_status=True)
            if ret.exit_status:
                logging.error("related bug url: %s", bug_url)
                raise error.TestFail("vcpupin failed: %s" % ret.stderr)
            virsh.vcpuinfo(vm_name, debug=True)
    finally:
        utils.run("service numad stop")
        libvirtd.restart()
        backup_xml.sync()
