# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0
#   Author: Transferred from tp-qemu nic_hotplug test case
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
from virttest import virsh
from virttest import utils_misc
from virttest import utils_net

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt

from provider.virtual_network import network_base

virsh_opt = {"debug": True, "ignore_status": False}


def run_sub_test(test, vm, params, plug_tag):
    """
    Run subtest before/after hotplug/unplug device.

    :param test: test object
    :param vm: VM object
    :param params: test parameters
    :param plug_tag: identify when to run subtest, ex, after_plug
    :return: whether vm was successfully shut-down if needed
    """
    sub_type = params.get("sub_type_%s" % plug_tag)
    login_timeout = params.get_numeric("login_timeout", 360)
    vm.cleanup_serial_console()
    vm.create_serial_console()
    session = vm.wait_for_serial_login(timeout=login_timeout)
    shutdown_method = params.get("shutdown_method", "shell")
    shutdown_command = params.get("shutdown_command")
    if sub_type == "reboot":
        test.log.info("Running sub test '%s' %s", sub_type, plug_tag)
        if params.get("reboot_method"):
            vm.reboot(session, params["reboot_method"], 0, login_timeout, serial=True)
    elif sub_type == "shutdown":
        test.log.info("Running sub test '%s' %s", sub_type, plug_tag)
        if shutdown_method == "shell":
            session.sendline(shutdown_command)
        if not utils_misc.wait_for(lambda: vm.state() == "shut off", 120):
            test.fail("VM failed to shutdown within timeout")


def run(test, params, env):
    """
    Test hotplug of NIC devices with shutdown/reboot operations.

    Transferred from tp-qemu nic_hotplug.one_pci.nic_virtio.with_shutdown/with_reboot
    and adapted to tp-libvirt structure and function usage.

    Test procedure:
    1) Boot a guest with interface
    2) Check connectivity
    3) Pause/resume VM and verify connectivity
    4) Hotunplug the interface
    5) Perform shutdown or reboot operation based on test variant

    :param test: QEMU test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    login_timeout = params.get_numeric("login_timeout", 360)
    repeat_times = params.get_numeric("repeat_times", 1)
    ping_params = {'vm_ping_host': 'pass'}
    ips = {'host_ip': utils_net.get_host_ip_address(params)}

    def run_test():
        """
        Execute the main interface hotplug with shutdown/reboot test.
        """
        for iteration in range(repeat_times):
            test.log.info("TEST_STEP 1:Start test iteration %s, guest xml", iteration + 1)
            vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
            test.log.debug("Prepare guest xml:%s\n", vmxml)
            iface = libvirt.get_vm_device(vmxml, "interface", index=-1)[0]

            test.log.info("TEST_STEP 2: Ping host from guest")
            session = vm.wait_for_login(timeout=login_timeout)
            network_base.ping_check(ping_params, ips, session, force_ipv4=True)

            test.log.info("TEST_STEP 3: Pause vm and resume and check connectivity again")
            virsh.suspend(vm_name, **virsh_opt)
            virsh.resume(vm_name, **virsh_opt)
            network_base.ping_check(ping_params, ips, session, force_ipv4=True)

            test.log.info("TEST_STEP 4: Ping host from guest after resume")
            network_base.ping_check(ping_params, ips, session, force_ipv4=True)
            session.close()

            test.log.info("TEST_STEP 5: Hot-unplug the interface")
            virsh.detach_device(vm_name, iface.xml, wait_for_event=True, **virsh_opt)
            run_sub_test(test, vm, params, "after_unplug")

    run_test()
