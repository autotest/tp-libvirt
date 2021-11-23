import logging

from avocado.core.exceptions import TestError, TestCancel
from avocado.utils import process

from virttest import libvirt_xml
from virttest import utils_misc
from virttest import utils_test
from virttest import virsh


def update_xml(vm_name, online_nodes, params):
    """
    Update XML with numatune and memoryBacking

    :param vm_name: Name of VM
    :param online_nodes: List of all online nodes with memory available
    """
    vmxml = libvirt_xml.VMXML.new_from_dumpxml(vm_name)
    memory_mode = params.get("memory_mode")
    numa_memory = {'mode': memory_mode,
                   'nodeset': online_nodes[1]}
    vmxml.numa_memory = numa_memory
    mb_xml = libvirt_xml.vm_xml.VMMemBackingXML()
    mb_xml.hugepages = libvirt_xml.vm_xml.VMHugepagesXML()
    vmxml.mb = mb_xml
    logging.debug("vm xml is %s", vmxml)
    vmxml.sync()


def setup_host(online_nodes, pages_list, ori_page_set):
    """
    Setup host for test - update number of hugepages and check

    :param online_nodes: List of all online nodes with memory available
    :param pages_list: List of required number of pages for particular nodes
    :param ori_page_set: A dict used to save original node page
    """
    index = 0
    if len(online_nodes) > 2:
        for pages in pages_list:
            ori_page_set[online_nodes[index]] = process.run(
                'cat /sys/devices/system/node/node{}/hugepages/hugepages-2048kB/nr_hugepages'.
                format(online_nodes[index]), shell=True).stdout_text.strip()
            logging.debug("ori_page_set is {}".format(ori_page_set))
            ret = process.run(
                'echo {} > /sys/devices/system/node/node{}/hugepages/hugepages-2048kB/nr_hugepages'.
                format(pages, online_nodes[index]), shell=True)
            if ret.exit_status:
                raise TestError('Cannot set {} hugepages on node {}'.
                                format(pages, online_nodes[index]))
            ret = process.run(
                'cat /sys/devices/system/node/node{}/hugepages/hugepages-2048kB/nr_hugepages'.
                format(online_nodes[index]), shell=True)
            if pages not in ret.stdout_text:
                raise TestError('Setting {} hugepages on node {} was unsuccessful'.
                                format(pages, online_nodes[index]))
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
    pages_list = eval(params.get('nodes_pages'))
    backup_xml = libvirt_xml.VMXML.new_from_dumpxml(vm_name)
    numa_info = utils_misc.NumaInfo()
    online_nodes = numa_info.get_online_nodes_withmem()
    ori_page_set = {}
    setup_host(online_nodes, pages_list, ori_page_set)
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
        for node_index, ori_page in ori_page_set.items():
            process.run(
                'echo {} > /sys/devices/system/node/node{}/hugepages/hugepages-2048kB/nr_hugepages'.
                format(ori_page, node_index), shell=True)
        backup_xml.sync()
