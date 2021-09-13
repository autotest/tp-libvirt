import logging

from virttest import libvirt_xml
from virttest import utils_misc

from virttest.utils_test import libvirt


def prepare_vm(vm_name, test):
    host_numa_node = utils_misc.NumaInfo()
    node_list = host_numa_node.online_nodes_withmem
    if len(node_list) < 2:
        test.cancel("Not enough numa nodes available")
    vmxml = libvirt_xml.VMXML.new_from_dumpxml(vm_name)
    numa_memnode = [
        {'cellid': '0', 'mode': 'strict', 'nodeset': str(node_list[0])},
        {'cellid': '1', 'mode': 'preferred', 'nodeset': str(node_list[1])}
    ]
    numa_cells = [
        {'id': '0', 'memory': '512000', 'unit': 'KiB'},
        {'id': '1', 'memory': '512000', 'unit': 'KiB'}
    ]
    vmxml.setup_attrs(**{
        'numa_memnode': numa_memnode,
        'cpu': {'reset_all': True, 'mode': 'host-model',
                'numa_cell': numa_cells}
    })
    logging.debug('VM XML prior test: {}'.format(vmxml))
    vmxml.sync()


def run(test, params, env):
    """
    Test numa node memory binding with automatic numa placement
    """

    logging.debug("The test has been started.")
    vm_name = params.get("main_vm")
    backup_xml = libvirt_xml.VMXML.new_from_dumpxml(vm_name)
    replace_string = params.get("replace_string", '')
    try:
        prepare_vm(vm_name, test)
        status = libvirt.exec_virsh_edit(vm_name, [replace_string])
        if status:
            test.fail(
                'Failure expected during virsh edit, but no failure occurs.')
        else:
            logging.info(
                'Virsh edit has failed, but that is intentional in '
                'negative cases.')
    except Exception as e:
        test.error("Unexpected error happened during the test execution: {}"
                   .format(e))
    finally:
        backup_xml.sync()
