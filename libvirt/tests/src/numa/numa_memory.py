import os
import logging
import platform
import random

from avocado.utils import process
from avocado.utils import path

from virttest import virt_vm
from virttest import libvirt_xml
from virttest import virsh
from virttest import utils_misc
from virttest import cpu
from virttest import utils_test
from virttest import utils_config
from virttest import utils_libvirtd
from virttest import data_dir


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
            test.fail("nodes memory usage not expected.")

    def format_affinity_str(cpu_list):
        """
        Format affinity str

        :param cpu_list: list of cpu number
        :return: cpu affinity string
        """
        cmd = "lscpu | grep '^CPU(s):'"
        ret = process.run(cmd, shell=True)
        cpu_num = int(ret.stdout_text.split(':')[1].strip())
        cpu_affinity_str = ""
        for i in range(cpu_num):
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
                host_node = utils_misc.NumaNode(i=node_num + 1)
                logging.debug("node %s cpu list is %s" %
                              (node_num, host_node.cpus))
                tmp_list += host_node.cpus
            cpu_list = [int(i) for i in tmp_list]
        if cpuset:
            cpu_list = cpuset
        ret = format_affinity_str(cpu_list)
        logging.debug("expect cpu affinity is %s", ret)
        if cpu_affinity != ret:
            test.fail("vcpuinfo cpu affinity not expected")

    def numa_mode_check(mode_nodeset):
        """
        when the mode = 'preferred' or 'interleave', it is better to check
        numa_maps.
        """
        vm_pid = vm.get_pid()
        numa_map = '/proc/%s/numa_maps' % vm_pid
        # Open a file
        with open(numa_map) as file:
            for line in file.readlines():
                if line.split()[1] != mode_nodeset:
                    test.fail("numa node and nodeset %s is "
                              "not expected" % mode_nodeset)

    vcpu_placement = params.get("vcpu_placement")
    vcpu_cpuset = params.get("vcpu_cpuset")
    bug_url = params.get("bug_url", "")
    status_error = "yes" == params.get("status_error", "no")
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    backup_xml = libvirt_xml.VMXML.new_from_dumpxml(vm_name)

    # Get host numa node list
    host_numa_node = utils_misc.NumaInfo()
    node_list = host_numa_node.online_nodes_withmem
    logging.debug("host node list is %s", " ".join(map(str, node_list)))

    # Prepare numatune memory parameter dict
    mem_tuple = ('memory_mode', 'memory_placement', 'memory_nodeset')
    numa_memory = {}
    for mem_param in mem_tuple:
        value = params.get(mem_param)
        if value:
            numa_memory[mem_param.split('_')[1]] = value
    arch = platform.machine()
    if 'ppc64' in arch:
        try:
            ppc_memory_nodeset = ""
            nodes = numa_memory["nodeset"]
            if '-' in nodes:
                for nnode in range(int(nodes.split('-')[0]), int(nodes.split('-')[1]) + 1):
                    ppc_memory_nodeset += str(node_list[nnode]) + ','
            else:
                node_lst = nodes.split(',')
                for nnode in range(len(node_lst)):
                    ppc_memory_nodeset += str(node_list[int(node_lst[nnode])]) + ','
            numa_memory["nodeset"] = ppc_memory_nodeset[:-1]
        except (KeyError, IndexError):
            pass

    # Prepare libvirtd session with log level as 1
    config_path = os.path.join(data_dir.get_tmp_dir(), "virt-test.conf")
    open(config_path, 'a').close()
    config = utils_config.LibvirtdConfig(config_path)
    config.log_level = 1
    arg_str = "--config %s" % config_path
    numad_reg = ".*numad"
    libvirtd = utils_libvirtd.LibvirtdSession(logging_handler=_logger,
                                              logging_pattern=numad_reg)

    try:
        libvirtd.start(arg_str=arg_str)
        # As libvirtd start as session use root, need stop virtlogd service
        # and start it as daemon to fix selinux denial
        try:
            path.find_command('virtlogd')
            process.run("service virtlogd stop", ignore_status=True)
            process.run("virtlogd -d")
        except path.CmdNotFoundError:
            pass

        # Allow for more times to libvirtd restarted sucessfully.
        ret = utils_misc.wait_for(lambda: libvirtd.is_working(),
                                  timeout=240,
                                  step=1)
        if not ret:
            test.fail("Libvirtd hang after restarted")

        # Get host cpu list
        tmp_list = []
        for node_num in node_list:
            host_node = utils_misc.NumaNode(i=node_num + 1)
            logging.debug("node %s cpu list is %s" %
                          (node_num, host_node.cpus))
            tmp_list += host_node.cpus
        cpu_list = [int(i) for i in tmp_list]

        dynamic_parameters = params.get('can_be_dynamic', 'no') == 'yes'

        if numa_memory.get('nodeset'):
            used_node = cpu.cpus_parser(numa_memory['nodeset'])
            logging.debug("set node list is %s", used_node)
            if not status_error:
                if not set(used_node).issubset(node_list):
                    if not dynamic_parameters:
                        test.cancel("nodeset %s out of range" % numa_memory['nodeset'])
                    else:
                        if '-' in numa_memory['nodeset']:
                            nodes_size = len(numa_memory['nodeset'].split('-'))
                        else:
                            nodes_size = len(numa_memory['nodeset'].split(','))
                        if nodes_size > len(node_list):
                            test.cancel("nodeset %s out of range" % numa_memory['nodeset'])
                        else:
                            numa_memory['nodeset'] = node_list[:nodes_size]

        if vcpu_cpuset:
            pre_cpuset = cpu.cpus_parser(vcpu_cpuset)
            logging.debug("Parsed cpuset list is %s", pre_cpuset)
            if not set(pre_cpuset).issubset(cpu_list):
                if not dynamic_parameters:
                    test.cancel("cpuset %s out of range" % vcpu_cpuset)
                else:
                    random_cpus = []
                    # Choose the random cpus from the list of available CPUs on the system and make sure no cpu is
                    # added twice or the list of selected CPUs is not long enough
                    for i in range(len([int(i) for i in vcpu_cpuset.split(',')])):
                        rand_cpu = random.randint(min(cpu_list), max(cpu_list))
                        while rand_cpu in random_cpus:
                            rand_cpu = random.randint(min(cpu_list), max(cpu_list))
                        random_cpus.append(rand_cpu)
                    random_cpus.sort()
                    vcpu_cpuset = (','.join([str(cpu_num) for cpu_num in random_cpus]))
                    pre_cpuset = cpu.cpus_parser(vcpu_cpuset)

        vmxml = libvirt_xml.VMXML.new_from_dumpxml(vm_name)
        vmxml.numa_memory = numa_memory
        vcpu_num = vmxml.vcpu
        max_mem = vmxml.max_mem
        if vmxml.xmltreefile.find('cputune'):
            vmxml.xmltreefile.remove_by_xpath('/cputune')
        else:
            logging.debug('No vcpupin found')
        if vcpu_placement:
            vmxml.placement = vcpu_placement
        if vcpu_cpuset:
            vmxml.cpuset = vcpu_cpuset
        logging.debug("vm xml is %s", vmxml)
        vmxml.sync()
        numad_cmd_opt = "-w %s:%s" % (vcpu_num, max_mem // 1024)

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
                test.fail("memory config %s not expected "
                          "after domain start" %
                          numa_memory_new)

            pos_vcpu_placement = vmxml_new.placement
            logging.debug("vcpu placement after domain start is %s",
                          pos_vcpu_placement)
            try:
                pos_cpuset = vmxml_new.cpuset
                logging.debug("vcpu cpuset after vm start is %s", pos_cpuset)
            except libvirt_xml.xcepts.LibvirtXMLNotFoundError:
                if vcpu_cpuset and vcpu_placement != 'auto':
                    test.fail("cpuset not found in domain xml.")

        except virt_vm.VMStartError as e:
            # Starting VM failed.
            if status_error:
                return
            else:
                test.fail("Test failed in positive case.\n "
                          "error: %s\n%s" % (e, bug_url))

        # Check qemu process numa memory usage
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
                    test.fail("cpu %s is not expected" % i)
            cpu_affinity_check(cpuset=pre_cpuset)
        if numa_memory.get('nodeset'):
            # If there are inconsistent node numbers on host,
            # convert it into sequence number so that it can be used
            # in mem_compare
            if numa_memory.get('mode') == 'strict':
                left_node = [node_list.index(i) for i in node_list if i not in used_node]
                used_node = [node_list.index(i) for i in used_node]
                mem_compare(used_node, left_node)
            elif numa_memory.get('mode') == 'preferred':
                mode_nodeset = 'prefer:' + numa_memory.get('nodeset')
                numa_mode_check(mode_nodeset)
            else:
                mode_nodeset = numa_memory.get('mode') + ':' + numa_memory.get('nodeset')
                numa_mode_check(mode_nodeset)
        logging.debug("numad log list is %s", numad_log)
        if vcpu_placement == 'auto' or numa_memory.get('placement') == 'auto':
            if not numad_log:
                test.fail("numad usage not found in libvirtd log")
            if numad_log[0].split("numad ")[-1] != numad_cmd_opt:
                logging.warning('numa log:\n%s\n' % numad_log[0].split("numad ")[-1])
                logging.warning('numa cmd opt:\n%s\n' % numad_cmd_opt)
                test.fail("numad command not expected in log")
            numad_ret = numad_log[1].split("numad: ")[-1]
            numad_node = cpu.cpus_parser(numad_ret)
            left_node = [node_list.index(i) for i in node_list if i not in numad_node]
            numad_node_seq = [node_list.index(i) for i in numad_node]
            logging.debug("numad nodes are %s", numad_node)
            if numa_memory.get('placement') == 'auto':
                if numa_memory.get('mode') == 'strict':
                    mem_compare(numad_node_seq, left_node)
                elif numa_memory.get('mode') == 'preferred':
                    mode_nodeset = 'prefer:' + numad_ret
                    numa_mode_check(mode_nodeset)
                else:
                    mode_nodeset = numa_memory.get('mode') + ':' + numad_ret
                    numa_mode_check(mode_nodeset)
            if vcpu_placement == 'auto':
                for i in left_node:
                    if qemu_cpu[i]:
                        test.fail("cpu usage in node %s is not expected" % i)
                cpu_affinity_check(node=numad_node)

    finally:
        try:
            path.find_command('virtlogd')
            process.run('pkill virtlogd', ignore_status=True)
            process.run('systemctl restart virtlogd.socket', ignore_status=True)
        except path.CmdNotFoundError:
            pass
        libvirtd.exit()
        if config_path:
            config.restore()
            if os.path.exists(config_path):
                os.remove(config_path)
        if vm.is_alive():
            vm.destroy(gracefully=False)
        backup_xml.sync()
