# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0
#   Author: Nannan Li<nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import time

from virttest import utils_net
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

VIRSH_ARGS = {'debug': True, 'ignore_status': False}


def get_init_iface_info(params, item):
    """
    Get guest initial interface info by item.

    :param params: Test parameters dictionary containing:
    :param item: The checking item.
    :return: return the item related interface info.
    """

    vm_name = params.get('main_vm')

    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    ifaces = vmxml.get_devices('interface')[0]

    if item == "pci":
        return ifaces.address.attrs
    elif item == "iface_mac":
        return ifaces.mac_address
    elif item == "iface_alias":
        return ifaces.alias.get('name')


def check_detach_result(test, params, vm):
    """
    Verify the detach operation result from xml and guest internal.

    This function validates whether the detach operation succeeded or failed as expected
    by counting the number of interfaces remaining in the VM XML after the operation.

    :param test: Test object for logging and test failure reporting
    :param params: Test parameters dictionary containing:
    :param vm: Vm object.
    :raises: test.fail if the interface count doesn't match expectations
    """
    vm_name = params.get('main_vm')
    status_error = "yes" == params.get("status_error")
    default_iface_mac = params.get('default_iface_mac')

    vm.cleanup_serial_console()
    # Give it time to cleanup, remove it would cause login failed for this case
    time.sleep(2)
    vm.create_serial_console()
    vm_session = vm.wait_for_serial_login(timeout=60)

    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    ifaces = vmxml.devices.by_device_tag('interface')

    iface_name = utils_net.get_linux_ifname(session=vm_session)[0]
    actual_iface_mac = utils_net.get_linux_mac(vm_session, iface_name)

    if not status_error:
        if len(ifaces) != 1:
            test.fail("Expected to get one interfaces after detach successfully")
        if default_iface_mac == actual_iface_mac:
            test.fail("Expected mac: %s was removed, but got %s" % (default_iface_mac, actual_iface_mac))

    else:
        if len(ifaces) != 2:
            test.fail("Expected to get two interface after detach failed")
        if default_iface_mac != actual_iface_mac:
            test.fail("Expected mac: %s was not removed, but got %s" % (default_iface_mac, actual_iface_mac))

    vm_session.close()
    test.log.debug("PASS: Get expected xml and guest internal interface after detaching")


def run(test, params, env):
    """
    Test detach interface with various MAC/PCI/alias combinations

    Test steps:
    1. Start VM with network interfaces
    2. Verify interface exists in guest with target MAC
    3. Attempt to detach interface using specified XML content
    4. For positive scenarios: verify detach succeeds and interface is removed
    5. For negative scenarios: verify detach fails with expected error message
    """
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    # Test parameters
    status_error = "yes" == params.get('status_error', 'no')
    detach_method = params.get('detach_method', 'detach-device')
    host_iface = utils_net.get_default_gateway(iface_name=True, force_dhcp=False, json=True).split()[0]

    add_iface = eval(params.get("add_iface", "{}") % host_iface)
    default_iface_alias = get_init_iface_info(params, 'iface_alias')
    default_iface_mac = get_init_iface_info(params, 'iface_mac')
    params.update({"default_iface_mac": default_iface_mac})

    if params.get("scenario") == "detach_different_type_correct_mac":
        detach_iface = eval(params.get('detach_iface', "{}") % host_iface)
    else:
        detach_iface = eval(params.get('detach_iface', "{}"))
    if params.get_boolean("add_pci"):
        pci_dict = get_init_iface_info(params, 'pci')
        detach_iface['address'] = {'attrs': pci_dict}

    try:
        test.log.info(f"TEST_SETUP: Prepare two interfaces and start vm")
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        libvirt_vmxml.modify_vm_device(vmxml, 'interface', add_iface, index=1)

        if not vm.is_alive():
            virsh.start(vm_name, **VIRSH_ARGS)
        vm.wait_for_login(timeout=240).close()
        test.log.debug("Prepare guest xml:%s", vm_xml.VMXML.new_from_dumpxml(vm_name))

        test.log.debug(f"TEST_STEP 1: Attempt to detach interface using {detach_method}")
        if detach_method == 'detach-device-alias':
            result = virsh.detach_device_alias(vm_name, default_iface_alias, extra='--live',
                                               debug=True)
        else:
            iface = libvirt_vmxml.create_vm_device_by_type('interface', detach_iface)
            test.log.debug("Detach XML content:%s", iface)
            result = virsh.detach_device(vm_name, iface.xml, flagstr='--live', debug=True)
        libvirt.check_exit_status(result, status_error)

        test.log.debug(f"TEST_STEP 2: Check interface after detaching")
        check_detach_result(test, params, vm)

    finally:
        test.log.debug("TEST_TEARDOWN: Restoring VM configuration")
        if vm.is_alive():
            virsh.destroy(vm_name)
        vmxml_backup.sync()
