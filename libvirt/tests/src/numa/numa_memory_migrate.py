import logging as log
import re

from avocado.core import exceptions
from avocado.utils import process

from virttest import virt_vm
from virttest import libvirt_xml
from virttest import virsh
from virttest import utils_misc
from virttest import cpu
from virttest import utils_test


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test numa memory migrate with live numa tuning
    """
    memory_status = []

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
            raise exceptions.TestFail("nodes memory usage not expected.")

    vm_name = params.get("main_vm")
    options = params.get("options", "live")
    vm = env.get_vm(vm_name)
    backup_xml = libvirt_xml.VMXML.new_from_dumpxml(vm_name)

    # Get host numa node list
    host_numa_node = utils_misc.NumaInfo()
    node_list = host_numa_node.online_nodes_withmem
    logging.debug("host node list is %s", node_list)
    if len(node_list) < 2:
        raise exceptions.TestSkipError("At least 2 numa nodes are needed on"
                                       " host")

    # Prepare numatune memory parameter dict
    mem_tuple = ('memory_mode', 'memory_placement', 'memory_nodeset')
    numa_memory = {}
    for mem_param in mem_tuple:
        value = params.get(mem_param)
        if value:
            numa_memory[mem_param.split('_')[1]] = value

    try:
        if numa_memory.get('nodeset'):
            used_node = cpu.cpus_parser(numa_memory['nodeset'])
            logging.debug("set node list is %s", used_node)
            for i in used_node:
                if i not in node_list:
                    raise exceptions.TestSkipError("nodeset %s out of range" %
                                                   numa_memory['nodeset'])

        vmxml = libvirt_xml.VMXML.new_from_dumpxml(vm_name)
        vmxml.numa_memory = numa_memory
        logging.debug("vm xml is %s", vmxml)
        vmxml.sync()

        try:
            vm.start()
            vm.wait_for_login().close()
        except virt_vm.VMStartError as e:
            raise exceptions.TestFail("Test failed in positive case.\n "
                                      "error: %s" % e)

        # get left used node beside current using
        if numa_memory.get('placement') == 'auto':
            cmd = "grep -E 'Nodeset returned from numad:' %s" % params.get("libvirtd_debug_file")
            cmdRes = process.run(cmd, shell=True, ignore_status=False)
            # Sample: Nodeset returned from numad: 0-1
            match_obj = re.search(r'Nodeset returned from numad:\s(.*)', cmdRes.stdout_text)
            numad_ret = match_obj.group(1)
            logging.debug("Nodeset returned from numad: %s", numad_ret)
            used_node = cpu.cpus_parser(numad_ret)
            logging.debug("numad nodes are %s", used_node)

        left_node = [i for i in node_list if i not in used_node]

        # run numatune live change numa memory config
        for node in left_node:
            virsh.numatune(vm_name, numa_memory.get('memory_mode'), str(node),
                           options, debug=True, ignore_status=False)

            vmxml_new = libvirt_xml.VMXML.new_from_dumpxml(vm_name)
            numa_memory_new = vmxml_new.numa_memory
            logging.debug("Current memory config dict is %s" % numa_memory_new)

            # Check xml config
            pos_numa_memory = numa_memory.copy()
            pos_numa_memory['nodeset'] = str(node)
            del pos_numa_memory['placement']
            logging.debug("Expect numa memory config is %s", pos_numa_memory)
            if pos_numa_memory != numa_memory_new:
                raise exceptions.TestFail("numa memory config %s not expected"
                                          " after live update" %
                                          numa_memory_new)

            # Check qemu process numa memory usage
            host_numa_node = utils_misc.NumaInfo()
            memory_status, qemu_cpu = utils_test.qemu.get_numa_status(
                host_numa_node,
                vm.get_pid())
            logging.debug("The memory status is %s", memory_status)
            # If there are inconsistent node numbers on host,
            # convert it into sequence number so that it can be used
            # in mem_compare
            # memory_status is a total numa list. node_list could not
            # match the count of nodes
            total_online_node_list = host_numa_node.online_nodes
            left_node_new = [total_online_node_list.index(i)
                             for i in total_online_node_list if i != node]
            used_node = [total_online_node_list.index(node)]

            mem_compare(used_node, left_node_new)

    finally:
        if vm.is_alive():
            vm.destroy(gracefully=False)
        backup_xml.sync()
