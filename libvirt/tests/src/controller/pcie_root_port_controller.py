import ast
import logging

from virttest import libvirt_version
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
    test_define_only = params.get("test_define_only") == "yes"

    vmxml.remove_all_device_by_type("controller")
    index = 0
    contr_dict = {"type": "pci", "model": "pcie-root", "index": index}
    libvirt_vmxml.modify_vm_device(vmxml, "controller", contr_dict, index)

    if test_define_only:
        return

    index = 1
    contr_dict = create_controller_dict(controller_model,
                                        controller_target, index)
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
        check_define_vm(vmxml, test, params)
    else:
        check_vm_start(vm, params)


def create_controller_dict(model, target, index, address=None):
    """
    Creates a dictionary for PCI controller.

    :param model: String, Controller model
    :param target: Dictionary, Controller target
    :param index: Int, controller index value
    :param address: Dict, optional, device address example: {'attrs': {'bus': 0x01}}
    :returns Built in dictionary, prepared to be added to VM XML
    """
    contr_dict = {"type": "pci",
                  "index": index}
    if target:
        contr_dict.update({"target": target})
    if model:
        contr_dict.update({"model": model})
    if address:
        contr_dict.update({"address": address})
    LOG.debug("Created controller %s", contr_dict)
    return contr_dict


def check_define_vm(vmxml, test, params):
    """
    Alternate function for checking and finishing the test.
    Checks only if VM define action was successful instead of VM start.

    :param vmxml: VM XML object
    :param test: Avocado test object
    :param params: Test parameters object
    """
    LOG.info("Checking VM in define only mode.")
    model = params.get("controller_model")
    controller_type = params.get("controller_type")
    target = params.get("controller_target")
    address = ast.literal_eval(params.get("controller_address", "{}"))
    bus_offset = int(params.get("bus_offset", 0))
    status_error = params.get("status_error") == "yes"
    failure_message = params.get("failure_message")
    minimal_interface_dict = ast.literal_eval(params.get("minimal_interface_dict", "{}"))
    interface_slot = params.get("interface_slot", 0)
    interface_slot_type = params.get("interface_slot_type", "int")
    check_slot = params.get("check_slot", "no") == "yes"
    wipe_devices = params.get("wipe_devices", "no") == "yes"
    if wipe_devices:
        wipe_pcie_controllers(vmxml)
    if interface_slot_type == "hex":
        interface_slot = hex(int(interface_slot))
    index = get_controller_index(vmxml, params)
    if address and "bus" not in address:
        address["bus"] = hex(index + bus_offset)
    slot_equal_after_define = params.get("slot_equal_after_define", "yes") == "yes"
    contr_dict = {'controller_type': controller_type,
                  "controller_index": index,
                  'controller_target': target,
                  "controller_addr": str(address)}
    if model:
        contr_dict.update({"controller_model": model})
    contr_object = libvirt.create_controller_xml(contr_dict)
    if index == 0:
        contr_object.index = 0  # Workaround for non-working avocado-vt
    vmxml.add_device(contr_object)
    if minimal_interface_dict:
        index += 1
        interface_dict = customize_interface_dict(minimal_interface_dict,
                                                  interface_bus=hex(index),
                                                  interface_slot=interface_slot)
        vmxml.devices = vmxml.devices.append(interface_dict)

    cmd_result = virsh.define(vmxml.xml)
    if failure_message:
        libvirt.check_result(cmd_result, [failure_message])
    else:
        libvirt.check_exit_status(cmd_result, status_error)
    if check_slot:
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(
            params.get("main_vm", "avocado-vt-vm1"))
        expected_value = address["slot"]
        if not check_slot_in_controller(vmxml, index, expected_value,
                                        slot_equal_after_define):
            test.fail("Controller slot doesn't have the"
                      "expected value of %s.", expected_value)


def check_slot_in_controller(vmxml, device_index, expected_value, expected_equal=True):
    """
    This function checks if address_slot value in a controller in VM XML equals
    expected value or not.

    :param vmxml: VM XML object to check
    :param device_index: Int, device index to check
    :param expected_value: The value that is used in the check
    :param expected_equal: Bool, true if expected_value should be equal to
    value in xml
    :returns Bool, based on the value comparison
    """
    controllers = vmxml.get_devices("controller")
    device = controllers[device_index]
    if expected_equal:
        return device.address.attrs["slot"] == expected_value
    return device.address.attrs["slot"] != expected_value


def get_controller_index(vmxml, params):
    """
    Function that finds index for a PCIe controller and returns it

    :param vmxml: VM XML object to check
    :param params: Test parameters object
    :returns Index number
    """
    # We want to default to None so we can differentiate between None and 0
    controller_index = params.get("controller_index", None)
    model = params.get("controller_model")
    if controller_index == "invalid_index":
        return controller_index
    if controller_index is not None:
        return int(controller_index)
    max_indexes = libvirt_pcicontr.get_max_contr_indexes(vmxml, 'pci', model, 1)
    if max_indexes:
        return max_indexes[0] + 1
    return 1


def customize_interface_dict(minimal_dict, interface_bus=None, interface_slot=None):
    """
    Update interface dictionary and prepare it for adding to VM XML.

    :param minimal_dict: Dictionary with interface values from config file
    :param interface bus: Optional value for address bus
    :param interface_slot: Optional value for address slot to update
    """
    if interface_bus or interface_slot:
        interface_address = {"attrs": {}}
        if interface_bus:
            interface_address["attrs"].update({"bus": interface_bus})
        if interface_slot:
            interface_address["attrs"].update({"slot": interface_slot})
        minimal_dict.update({"address": interface_address})
    iface = libvirt_vmxml.create_vm_device_by_type("interface", minimal_dict)
    return iface


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


def check_vm_start(vm, params, **virsh_options):
    """
    Start the vm and check if exception occurred.

    :param vm: Avocado vm object
    :param params: Avocado test parameters object
    :param **virsh_options: Other parameters to hand to virsh start command
    """
    status_error = params.get("status_error") == "yes"
    failure_message = params.get("failure_message")
    LOG.debug("Starting VM with XML:\n%s", vm_xml.VMXML.new_from_dumpxml(vm.name))
    cmd_result = virsh.start(vm.name, **virsh_options)
    if failure_message:
        libvirt.check_result(cmd_result, [failure_message])
    else:
        libvirt.check_exit_status(cmd_result, status_error)


def wipe_pcie_controllers(vmxml):
    """
    Function that removes all controller devices from VM XML.
    It then re-adds pcie-root, so that pcie-root-ports can be easily added.

    :param vmxml: VM XML object to check
    """

    vmxml.remove_all_device_by_type("controller")
    index = 0
    contr_dict = {"type": "pci", "model": "pcie-root", "index": index}
    libvirt_vmxml.modify_vm_device(vmxml, "controller", contr_dict, index)


def run(test, params, env):
    """
    Function executed by avocado. Similar to "main" function of a module.

    :params test: Test object of Avocado framework
    :params params: Object containing parameters of a test from cfg file
    :params env: Environment object from Avocado framework
    """
    libvirt_version.is_libvirt_feature_supported(params)
    vm = env.get_vm(params.get("main_vm", "avocado-vt-vm1"))
    vmxml_backup = vm_xml.VMXML.new_from_dumpxml(vm.name)
    test_define_only = params.get("test_define_only")

    if not test_define_only:
        setup_test(params)
    try:
        execute_test(vm, test, params)
    finally:
        cleanup_test(vm, vmxml_backup)
