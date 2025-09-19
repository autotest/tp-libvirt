# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0
#   Author: Nannan Li<nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
from virttest import utils_misc
from virttest import utils_net
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml

from provider.virtual_network import network_base

VIRSH_ARGS = {'ignore_status': False, 'debug': True}


def run(test, params, env):
    """
    Test hotplug ethernet type interface by attach-device

    Steps:
    1. Start the vm without any interface
    2. After vm boot successfully, hotplug the interface and check xpath
    3. Guest ping check
    4. Detach the interface and check no interface result
    """

    def setup_test():
        """Setup VM without any interfaces and create required tap devices"""
        test.log.info("TEST_SETUP: Setting up VM without interfaces")
        libvirt_vmxml.remove_vm_devices_by_type(vm, "interface")
        vm.start()

    def run_test():
        """Main test steps: attach interface, check, ping, detach"""
        test.log.info("TEST_STEP1: Attach interface")
        iface_attrs = eval(params.get('iface_attrs', "{}"))
        if device_type == "tap":
            network_base.create_tap(tap_name, default_br, "root")
        elif device_type == "macvtap":
            mac_addr = network_base.create_macvtap(macvtap_name, host_iface, "root")
            iface_attrs["mac_address"] = mac_addr

        iface = libvirt_vmxml.create_vm_device_by_type('interface', iface_attrs)
        virsh.attach_device(vm_name, iface.xml, **VIRSH_ARGS)
        test.log.debug("Guest xml before testing:%s", vm_xml.VMXML.new_from_dumpxml(vm_name))

        test.log.info("TEST_STEP2: Checking guest interface added")
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        libvirt_vmxml.check_guest_xml_by_xpaths(vmxml, expected_xpaths_attach)

        session = vm.wait_for_serial_login()
        if not utils_misc.wait_for(
            lambda: len(utils_net.get_linux_iface_info(session=session)) == 2, timeout=15
        ):
            test.fail(f'Interface should be in guest')

        test.log.info("TEST_STEP3: Checking guest connectivity")

        def _ping_check_wrapper():
            try:
                ips = {'outside_ip': params.get('outside_ip')}
                network_base.ping_check(params, ips, session, force_ipv4=True)
                return True
            except Exception:
                return False
        if not utils_misc.wait_for(_ping_check_wrapper, timeout=10, step=1):
            test.fail("Guest ping outside failed")
        session.close()

        test.log.info("TEST_STEP4: Detaching ethernet interface and checking interface removal")
        virsh.detach_device(vm_name, iface.xml, wait_for_event=True, **VIRSH_ARGS)

        libvirt_vmxml.check_guest_xml_by_xpaths(
            vm_xml.VMXML.new_from_dumpxml(vm_name), expected_xpaths_detach
        )
        session = vm.wait_for_serial_login()
        if not utils_misc.wait_for(
            lambda: len(utils_net.get_linux_iface_info(session=session)) == 1, timeout=15
        ):
            test.fail(f'Interface should be removed from guest')
        test.log.info("Interface successfully removed from guest")
        session.close()

    def teardown_test():
        """Cleanup test environment"""
        test.log.info("TEST_TEARDOWN: Cleaning up test environment")
        bkxml.sync()
        if device_type == "tap":
            test.log.info(f"Cleaning up tap device: {tap_name}")
            network_base.delete_tap(tap_name)
        elif device_type == "macvtap":
            test.log.info(f"Cleaning up macvtap device: {macvtap_name}")
            network_base.delete_tap(macvtap_name)

    # Get VM parameters
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)
    device_type = params.get('device_type')
    default_br = params.get('default_br')
    tap_name = params.get('tap_name', 'mytap0')
    macvtap_name = params.get('macvtap_name', 'mymacvtap0')
    expected_xpaths_attach = eval(params.get('expected_xpaths_attach'))
    expected_xpaths_detach = eval(params.get('expected_xpaths_detach'))
    if utils_misc.wait_for(
            lambda: utils_net.get_default_gateway(
                iface_name=True, force_dhcp=True, json=True) is None, timeout=15):
        test.fail("Not find host interface in 15s")
    host_iface = params.get("host_iface")
    host_iface = host_iface if host_iface else utils_net.get_default_gateway(
        iface_name=True, force_dhcp=True).split()[0]
    # Backup original VM XML
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    try:
        setup_test()
        run_test()
    finally:
        teardown_test()
