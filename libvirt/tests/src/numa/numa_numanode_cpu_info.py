import logging as log

from avocado.core.exceptions import TestError, TestCancel
from avocado.utils import process

from virttest import libvirt_xml
from virttest import utils_misc
from virttest import utils_test
from virttest import virsh
from virttest.staging import utils_memory


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def update_xml(vm_name, online_nodes, params):
    """
    Update XML with numatune and memoryBacking

    :param vm_name: Name of VM
    :param online_nodes: List of all online nodes with memory available
    """
    vmxml = libvirt_xml.VMXML.new_from_dumpxml(vm_name)
    memory_mode = params.get("memory_mode")
    memory_size = int(params.get("memory_size"))
    current_memory_size = int(params.get("current_memory_size"))
    numa_memory = {'mode': memory_mode,
                   'nodeset': online_nodes[1]}
    vmxml.numa_memory = numa_memory
    mb_xml = libvirt_xml.vm_xml.VMMemBackingXML()
    mb_xml.hugepages = libvirt_xml.vm_xml.VMHugepagesXML()
    vmxml.mb = mb_xml
    vmxml.memory = memory_size
    vmxml.current_mem = current_memory_size
    logging.debug("vm xml is %s", vmxml)
    vmxml.sync()


def setup_host(required_node_num, online_nodes, memory_list, ori_page_set):
    """
    Setup host for test - update number of hugepages and check

    :param required_node_num: int, numa node number at least on the host required by the test
    :param online_nodes: List of all online nodes with memory available
    :param memory_list: List of required hugepage memory for particular nodes
    :param ori_page_set: A dict used to save original node page
    """
    index = 0

    if len(online_nodes) >= required_node_num:
        hugepage_size = utils_memory.get_huge_page_size()
        for memory_size in memory_list:
            ori_page_set[online_nodes[index]] = process.run(
                'cat /sys/devices/system/node/node{}/hugepages/hugepages-{}kB/nr_hugepages'.
                format(online_nodes[index], hugepage_size), shell=True).stdout_text.strip()
            logging.debug("ori_page_set is {}".format(ori_page_set))
            pages = int(int(memory_size) / hugepage_size)
            ret = process.run(
                'echo {} > /sys/devices/system/node/node{}/hugepages/hugepages-{}kB/nr_hugepages'.
                format(pages, online_nodes[index], hugepage_size), shell=True)
            if ret.exit_status:
                raise TestError('Cannot set {} pages for {}kB huge page on node {}'.
                                format(pages, hugepage_size, online_nodes[index]))
            ret = process.run(
                'cat /sys/devices/system/node/node{}/hugepages/hugepages-{}kB/nr_hugepages'.
                format(online_nodes[index], hugepage_size), shell=True)
            if str(pages) not in ret.stdout_text:
                raise TestError('Setting {} pages for {}kB huge page on node {} was unsuccessful'.
                                format(pages, hugepage_size, online_nodes[index]))
            index += 1
    else:
        raise TestCancel("The test cannot continue since there is no enough "
                         "available NUMA nodes with memory.")


def run(test, params, env):
    """
    Test the numanode info in cpu section.
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    error_message = params.get("err_msg")
    node_memory_list = eval(params.get('nodes_memory'))
    backup_xml = libvirt_xml.VMXML.new_from_dumpxml(vm_name)
    numa_info = utils_misc.NumaInfo()
    online_nodes = numa_info.get_online_nodes_withmem()
    ori_page_set = {}
    required_numa_node_num = int(params.get("numa_cells_with_memory_required", '2'))
    setup_host(required_numa_node_num, online_nodes, node_memory_list, ori_page_set)
    try:
        if vm.is_alive():
            vm.destroy()
        update_xml(vm_name, online_nodes, params)
        # Try to start the VM with invalid node, fail is expected
        ret = virsh.start(vm_name, ignore_status=True)
        utils_test.libvirt.check_status_output(ret.exit_status,
                                               ret.stderr_text,
                                               expected_fails=[error_message])
    except Exception as e:
        test.error("Unexpected error: {}".format(e))
    finally:
        hugepage_size = utils_memory.get_huge_page_size()
        for node_index, ori_page in ori_page_set.items():
            process.run(
                'echo {} > /sys/devices/system/node/node{}/hugepages/hugepages-{}kB/nr_hugepages'.
                format(ori_page, node_index, hugepage_size), shell=True)
        backup_xml.sync()
