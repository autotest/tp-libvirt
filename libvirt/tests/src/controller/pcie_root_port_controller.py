import ast
import logging

from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml, libvirt_pcicontr
from virttest.utils_test import libvirt


LOG = logging.getLogger('avocado.' + __name__)


def setup_test(params):
    """
    Perform step needed before test can be executed.

    :param params: Test parameters object
    """
    controller_model = params.get("controller_model")
    controller_target = ast.literal_eval(params.get("controller_target"))
    second_controller_model = params.get("second_controller_model")
    second_controller_target = ast.literal_eval(
        params.get("second_controller_target", "{}"))
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(params.get("main_vm", "avocado-vt-vm1"))

    vmxml.remove_all_device_by_type("controller")

    index = 0
    contr_dict = {"type": "pci", "model": "pcie-root", "index": index}
    libvirt_vmxml.modify_vm_device(vmxml, "controller", contr_dict, index)

    index += 1
    contr_dict = create_controller_dict(controller_model, controller_target, index)
    libvirt_vmxml.modify_vm_device(vmxml, "controller", contr_dict, index)

    if second_controller_model and second_controller_target:
        index += 1
        contr_dict = create_controller_dict(second_controller_model,
                                            second_controller_target, index)
        libvirt_vmxml.modify_vm_device(vmxml, "controller", contr_dict, index)

    vmxml.sync()


def execute_test(vm, test, params):
    """
    Perform the checks that make the case.

    :param vm: VM object from avocado
    :param test: Avocado test object
    :param params: Test parameters object
    """
    test_define_only = params.get("test_define_only") == "yes"
    if test_define_only:
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm.name)
        check_define_vm(vmxml, params, test)
    else:
        status_error = params.get("status_error") == "yes"
        LOG.debug("Starting VM with XML:\n%s", vm_xml.VMXML.new_from_dumpxml(vm.name))
        try:
            vm.start()
        except Exception as exc:
            if status_error:
                LOG.info("VM failed to start as expected.")
            else:
                test.fail("VM failed to start, reason: %s" % exc)
        else:
            if vm.is_alive() and status_error:
                test.fail("VM started sucessfully, but shouldn't.")


def create_controller_dict(model, target, index, address=None):
    """
    Creates a dictionary for PCI controller.

    :param model: String, Controller model
    :param target: Dictionary, Controller target
    :param index: Int, controller index value
    :param address: Dict, optional, device address example: {'attrs': {'bus': 0x01}}
    :returns Built in dictionary, prepared to be added to VM XML
    """
    contr_dict = {'type': 'pci',
                  'model': model,
                  "index": index,
                  'target': target}
    if address:
        contr_dict.update({"address": address})
    return contr_dict


def check_define_vm(vmxml, params, test):
    """
    Alternate function for checking and finishing the test.
    Checks only if VM define action was successful instead of VM start.

    :param vmxml: VM XML object
    :param params: Test parameters object
    :param test: Avocado test object
    """
    model = params.get("controller_model")
    target = params.get("controller_target")
    address = ast.literal_eval(params.get("controller_address", "{}"))
    bus_offset = int(params.get("bus_offset", 0))
    status_error = params.get("status_error") == "yes"
    failure_message = params.get("failure_message")
    max_indexes = libvirt_pcicontr.get_max_contr_indexes(vmxml, 'pci', model, 1)
    index = max_indexes[0] + 1
    address["bus"] = hex(index + bus_offset)
    contr_dict = {'controller_type': 'pci',
                  'controller_model': model,
                  "controller_index": index,
                  'controller_target': target,
                  "controller_addr": str(address)}
    contr_object = libvirt.create_controller_xml(contr_dict)
    vmxml.add_device(contr_object)
    cmd_result = virsh.define(vmxml.xml)
    if failure_message:
        libvirt.check_result(cmd_result, [failure_message])
    else:
        libvirt.check_exit_status(cmd_result, status_error)


def cleanup_test(vm, vmxml_backup):
    """
    Reconfigure the environment back to the state if was in before test setup.

    :param vm: VM object of the VM we're operating on
    :param vmxml_backup: Backup vmxml to restore to
    """
    LOG.info("Start cleanup")
    if vm.is_alive():
        vm.destroy()
    LOG.info("Restore the VM XML")
    vmxml_backup.sync()


def run(test, params, env):
    """
    Function executed by avocado. Similar to "main" function of a module.

    :params test: Test object of Avocado framework
    :params params: Object containing parameters of a test from cfg file
    :params env: Environment object from Avocado framework
    """
    vm = env.get_vm(params.get("main_vm", "avocado-vt-vm1"))
    vmxml_backup = vm_xml.VMXML.new_from_dumpxml(vm.name)
    test_define_only = params.get("test_define_only")

    if not test_define_only:
        setup_test(params)
    try:
        execute_test(vm, test, params)
    finally:
        cleanup_test(vm, vmxml_backup)
