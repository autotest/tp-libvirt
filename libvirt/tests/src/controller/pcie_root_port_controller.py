import logging
import ast

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml


LOG = logging.getLogger('avocado.' + __name__)


def setup_test(params):
    """
    Perform step needed before test can be executed.

    :param params: Test parameters object
    """
    controller_model = params.get("controller_model")
    controller_target = ast.literal_eval(params.get("controller_target"))
    second_controller_model = params.get("second_controller_model")
    second_controller_target = ast.literal_eval(params.get("second_controller_target"))
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(params.get("main_vm", "avocado-vt-vm1"))
    index = 0

    vmxml.remove_all_device_by_type("controller")

    contr_dict = {"type": "pci", "model": "pcie-root", "index": index}
    libvirt_vmxml.modify_vm_device(vmxml, "controller", contr_dict, index)

    index += 1
    contr_dict = {'type': 'pci',
                  'model': controller_model,
                  "index": index,
                  'target': controller_target}
    libvirt_vmxml.modify_vm_device(vmxml, "controller", contr_dict, index)

    index += 1
    if second_controller_model and second_controller_target:
        contr_dict = {'type': 'pci',
                      'model': second_controller_model,
                      "index": index,
                      'target': second_controller_target}
        libvirt_vmxml.modify_vm_device(vmxml, "controller", contr_dict, index)

    vmxml.sync()


def execute_test(vm, test, params):
    """
    Perform the checks that make the case.

    :param vm: VM object from avocado
    :param test: Avocado test object
    :param params: Test parameters object
    """
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

    setup_test(params)
    try:
        execute_test(vm, test, params)
    finally:
        cleanup_test(vm, vmxml_backup)
