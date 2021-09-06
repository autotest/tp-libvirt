import logging

from virttest.utils_test import libvirt
from virttest import libvirt_xml
from virttest import utils_test
from virttest import virsh


def define_and_check_xml(vmxml, params):
    """
    Prepare the XML so it has numa nodes and hugepages setup properly. Try
    to define the VM from the XML and check for the status (either success or
    failure might be considered).

    :param vmxml: Original VM to edit
    :param params: Avocado params object
    """
    # Prepare a numa cells and hugepages lists
    numa_cells = []
    for key in params:
        if "cell_id" in key:
            numa_cells.append(eval(params[key]))
    pages = [eval(params["page_id_0"])]
    logging.debug(numa_cells, pages)

    del vmxml.numa_memory
    vmcpuxml = libvirt_xml.vm_xml.VMCPUXML()
    vmcpuxml.xml = "<cpu mode='host-model'><numa/></cpu>"
    vmcpuxml.numa_cell = vmcpuxml.dicts_to_cells(numa_cells)
    logging.debug("cpu xml is %s", vmcpuxml)
    vmxml.cpu = vmcpuxml

    mem_backing = libvirt_xml.vm_xml.VMMemBackingXML()
    hugepages = libvirt_xml.vm_xml.VMHugepagesXML()
    pagexml = hugepages.PageXML()
    pagexml_list = []
    for page in pages:
        pagexml.update(page)
        pagexml_list.append(pagexml)
    hugepages.pages = pagexml_list
    mem_backing.hugepages = hugepages
    logging.debug('membacking xml is: %s', mem_backing)
    vmxml.mb = mem_backing

    logging.debug("final vm xml is %s", vmxml)
    ret = virsh.define(vmxml.xml)
    error_message = params.get('err_message')
    utils_test.libvirt.check_status_output(ret.exit_status,
                                           ret.stderr_text,
                                           expected_fails=error_message)


def edit_vm(vm_name, test):
    """
    Edit the running VM using the virsh edit feature and the VI editor and
    verify the status. Command failure is expected behavior.

    :param vm_name: The name of the VM to edit
    :param test: Avocado test object
    """
    replace_string = r":%s:memAccess='shared':memAccess='invalid':"
    status = libvirt.exec_virsh_edit(vm_name, [replace_string])
    if status:
        test.fail('Failure expected during virsh edit, but no failure occurs.')
    else:
        logging.info('Virsh edit has failed, but that is intentional in '
                     'negative cases.')


def run(test, params, env):
    """
    Check memAccess when parsing cpu.
    """
    vm_name = params.get("main_vm")
    backup_xml = libvirt_xml.VMXML.new_from_dumpxml(vm_name)
    edit_test = params.get('edit_test', '') == 'yes'
    try:
        vmxml = backup_xml.copy()
        define_and_check_xml(vmxml, params)
        if edit_test:
            vm = env.get_vm(vm_name)
            vm.wait_for_login().close()
            edit_vm(vm_name, test)
    except Exception as e:
        test.error('Unexpected error: {}'.format(e))
    finally:
        backup_xml.sync()
