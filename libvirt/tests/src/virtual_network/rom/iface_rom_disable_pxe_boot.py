# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0
#   Author: Nannan Li<nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import re

from virttest import utils_net
from virttest import utils_misc
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml

from provider.guest_os_booting import guest_os_booting_base as guest_os


def run(test, params, env):
    """
    Test ROM disable PXE boot for bridge type interface.
    """

    def setup_test():
        """
        Setup test environment and VM configuration
        """
        test.log.info("TEST_SETUP: Configure firmware and boot order")
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        vmxml.remove_all_boots()
        vmxml.set_boot_order_by_target_dev(
            vmxml.get_devices('disk')[0].target.get('dev'), disk_order)
        libvirt_vmxml.modify_vm_device(vmxml, 'interface', iface_attrs)

        guest_os.prepare_os_xml(
            vm_name, os_dict=os_attrs, firmware_type=firmware_type)
        test.log.debug("Test guest xml:%s", vm_xml.VMXML.new_from_inactive_dumpxml(vm_name))

    def run_test():
        """
        Check guest xml and pxe boot msg.
        """
        test.log.info("TEST_STEP 1: Checking VM XML configuration")
        libvirt_vmxml.check_guest_xml_by_xpaths(
            vm_xml.VMXML.new_from_inactive_dumpxml(vm_name), expected_xpaths)

        test.log.debug("TEST_STEP 2: Verifying VM boots message")
        virsh_session = virsh.VirshSession(virsh_exec=virsh.VIRSH_EXEC, auto_close=True)
        virsh_session.sendline("start %s --console" % vm_name)
        if not utils_misc.wait_for(
                lambda: re.search(boot_msg, virsh_session.get_output()), 15
        ):
            if not (model in ["rtl8139", "e1000e"] and firmware == "uefi"):
                test.fail("Boot msg:%s checking has failed:%s " % (boot_msg, virsh_session.get_output()))
        test.log.info("Boot msg:%s checking has PASSED", boot_msg)

    def teardown_test():
        """
        Clean up test environment
        """
        test.log.debug("TEST_TEARDOWN: Recover the env.")
        utils_net.delete_linux_bridge_tmux(bridge_name, host_iface)
        bkxml.sync()

    vm_name = guest_os.get_vm(params)
    os_attrs = eval(params.get("os_attrs", "{}"))
    model = params.get("model")
    firmware = params.get("firmware")
    boot_msg = params.get("boot_msg")
    expected_xpaths = eval(params.get("expected_xpaths"))
    rand_id = utils_misc.generate_random_string(3)
    bridge_type = params.get('bridge_type', '')
    bridge_name = bridge_type + '_' + rand_id

    if not utils_misc.wait_for(
            lambda: utils_net.get_default_gateway(
                iface_name=True, force_dhcp=True, json=True) is not None, timeout=15):
        test.fail("Cannot get default gateway in 15s")
    host_iface = params.get("host_iface")
    iface_name = host_iface if host_iface else utils_net.get_default_gateway(
        iface_name=True, force_dhcp=True).split()[0]
    utils_net.create_linux_bridge_tmux(bridge_name, iface_name)
    iface_attrs = eval(params.get("iface_attrs") % bridge_name)
    disk_order = int(params.get("disk_order"))
    firmware_type = params.get("firmware_type")

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:

        setup_test()
        run_test()

    finally:
        teardown_test()
