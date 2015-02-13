import os
import logging
from virttest import virt_vm
from virttest import libvirt_xml
from virttest import virsh
from virttest import utils_misc
from virttest import utils_test
from virttest import utils_config
from virttest import utils_libvirtd
from autotest.client import utils
from autotest.client.shared import error


def run(test, params, env):
    """
    Test numa tuning with memory
    """
    numad_log = []
    memory_status = []

    def _logger(line):
        """
        Callback function to log libvirtd output.
        """
        numad_log.append(line)

    def mem_compare(used_node, left_node):
        """
        Memory in used nodes should greater than left nodes

        :param used_node: used node list
        :param left_node: left node list
        """
        used_mem_total = 0
        left_node_mem_total = 0
        for i in used_node:
            used_mem_total += int(memory_status[i])
        for i in left_node:
            left_node_mem_total += int(memory_status[i])
        if left_node_mem_total > used_mem_total:
            raise error.TestFail("nodes memory usage not expected.")

    def format_affinity_str(cpu_list):
        """
        Format affinity str

        :param cpu_list: list of cpu number
        :return: cpu affinity string
        """
        cpu_affinity_str = ""
        host_cpu_count = utils.count_cpus()
        for i in range(host_cpu_count):
            if i in cpu_list:
                cpu_affinity_str += "y"
            else:
                cpu_affinity_str += "-"
        return cpu_affinity_str

    def cpu_affinity_check(cpuset=None, node=None):
        """
        Check vcpuinfo cpu affinity

        :param cpuset: cpuset list
        :param node: node number list
        """
        result = virsh.vcpuinfo(vm_name, debug=True)
        output = result.stdout.strip().splitlines()[-1]
        cpu_affinity = output.split(":")[-1].strip()
        if node:
            tmp_list = []
            for node_num in node:
                host_node = utils_misc.NumaNode(i=node_num+1)
                logging.debug("node %s cpu list is %s" %
                              (node_num, host_node.cpus))
                tmp_list += host_node.cpus
            cpu_list = [int(i) for i in tmp_list]
        if cpuset:
            cpu_list = cpuset
        ret = format_affinity_str(cpu_list)
        logging.debug("expect cpu affinity is %s", ret)
        if cpu_affinity != ret:
            raise error.TestFail("vcpuinfo cpu affinity not expected")

    vcpu_placement = params.get("vcpu_placement")
    vcpu_cpuset = params.get("vcpu_cpuset")
    bug_url = params.get("bug_url", "")
    status_error = "yes" == params.get("status_error", "no")
    vm_name = params.get("vms")
    vm = env.get_vm(vm_name)
    backup_xml = libvirt_xml.VMXML.new_from_dumpxml(vm_name)

    # Prepare numatune memory parameter dict
    mem_tuple = ('memory_mode', 'memory_placement', 'memory_nodeset')
    numa_memory = {}
    for mem_param in mem_tuple:
        value = params.get(mem_param)
        if value:
            numa_memory[mem_param.split('_')[1]] = value

    # Prepare libvirtd session with log level as 1
    config_path = "/var/tmp/virt-test.conf"
    open(config_path, 'a').close()
    config = utils_config.LibvirtdConfig(config_path)
    config.log_level = 1
    arg_str = "--config %s" % config_path
    numad_reg = ".*numad"
    libvirtd = utils_libvirtd.LibvirtdSession(logging_handler=_logger,
                                              logging_pattern=numad_reg)

    try:
        libvirtd.start(arg_str=arg_str)

        # Get host numa node list
        host_numa_node = utils_misc.NumaInfo()
        node_list = host_numa_node.online_nodes
        logging.debug("host node list is %s", node_list)
        if numa_memory.get('nodeset'):
            used_node = utils_test.libvirt.cpus_parser(numa_memory['nodeset'])
            logging.debug("set node list is %s", used_node)
            if not status_error:
                for i in used_node:
                    if i > max(node_list):
                        raise error.TestNAError("nodeset %s out of range" %
                                                numa_memory['nodeset'])

        vmxml = libvirt_xml.VMXML.new_from_dumpxml(vm_name)
        vmxml.numa_memory = numa_memory
        vcpu_num = vmxml.vcpu
        current_mem = vmxml.current_mem
        if vcpu_placement:
            vmxml.placement = vcpu_placement
        if vcpu_cpuset:
            vmxml.cpuset = vcpu_cpuset
            pre_cpuset = utils_test.libvirt.cpus_parser(vcpu_cpuset)
            logging.debug("Parsed cpuset list is %s", pre_cpuset)
        logging.debug("vm xml is %s", vmxml)
        vmxml.sync()
        numad_cmd_opt = "-w %s:%s" % (vcpu_num, current_mem/1024)

        try:
            vm.start()
            vm.wait_for_login()
            vmxml_new = libvirt_xml.VMXML.new_from_dumpxml(vm_name)
            numa_memory_new = vmxml_new.numa_memory
            logging.debug("Current memory config dict is %s" % numa_memory_new)

            # Check xml config
            if numa_memory.get('placement') == 'static':
                pre_numa_memory = numa_memory.copy()
                del pre_numa_memory['placement']
            else:
                pre_numa_memory = numa_memory

            if pre_numa_memory != numa_memory_new:
                raise error.TestFail("memory config %s not expected after "
                                     "domain start" % numa_memory_new)

            pos_vcpu_placement = vmxml_new.placement
            logging.debug("vcpu placement after domain start is %s",
                          pos_vcpu_placement)
            try:
                pos_cpuset = vmxml_new.cpuset
                logging.debug("vcpu cpuset after vm start is %s", pos_cpuset)
            except libvirt_xml.xcepts.LibvirtXMLNotFoundError:
                if vcpu_cpuset and vcpu_placement != 'auto':
                    raise error.TestFail("cpuset not found in domain xml.")

        except virt_vm.VMStartError, e:
            # Starting VM failed.
            if status_error:
                return
            else:
                raise error.TestFail("Test failed in positive case.\n error:"
                                     " %s\n%s" % (e, bug_url))

        # Check qemu process numa memory usage
        host_numa_node = utils_misc.NumaInfo()
        memory_status, qemu_cpu = utils_test.qemu.get_numa_status(
            host_numa_node,
            vm.get_pid())
        logging.debug("The memory status is %s", memory_status)
        logging.debug("The cpu usage is %s", qemu_cpu)

        if vcpu_cpuset:
            total_cpu = []
            for node_cpu in qemu_cpu:
                total_cpu += node_cpu
            for i in total_cpu:
                if int(i) not in pre_cpuset:
                    raise error.TestFail("cpu %s is not expected" % i)
            cpu_affinity_check(cpuset=pre_cpuset)
        if numa_memory.get('nodeset'):
            left_node = [i for i in node_list if i not in used_node]
            mem_compare(used_node, left_node)

        logging.debug("numad log list is %s", numad_log)
        if vcpu_placement == 'auto' or numa_memory.get('placement') == 'auto':
            if not numad_log:
                raise error.TestFail("numad usage not found in libvirtd log")
            if numad_log[0].split("numad ")[-1] != numad_cmd_opt:
                raise error.TestFail("numad command not expected in log")
            numad_ret = numad_log[1].split("numad: ")[-1]
            numad_node = utils_test.libvirt.cpus_parser(numad_ret)
            left_node = [i for i in node_list if i not in numad_node]
            logging.debug("numad nodes are %s", numad_node)
            if numa_memory.get('placement') == 'auto':
                mem_compare(numad_node, left_node)
            if vcpu_placement == 'auto':
                for i in left_node:
                    if qemu_cpu[i]:
                        raise error.TestFail("cpu usage in node %s is not "
                                             "expected" % i)
                cpu_affinity_check(node=numad_node)

    finally:
        libvirtd.exit()
        if config_path:
            config.restore()
            if os.path.exists(config_path):
                os.remove(config_path)
        if vm.is_alive():
            vm.destroy(gracefully=False)
        backup_xml.sync()
