# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0
#   Author: Nannan Li<nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

import os
import re

from virttest import data_dir
from virttest import utils_misc
from virttest import utils_net
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from provider.interface import interface_base

from provider.virtual_network import network_base

VIRSH_ARGS = {'debug': True, 'ignore_status': False}


def check_guest_internal_value(test, vm, check_item, expected_value, params):
    """
    Check guest internal configuration values

    :param test: test instance
    :param vm: VM instance
    :param check_item: type of check (multiqueue, mtu, qos)
    :param expected_value: expected value to verify
    :param params: params object
    """
    if vm.serial_console is None:
        vm.create_serial_console()
    vm_session = vm.wait_for_serial_login()
    iface_dict = eval(params.get('iface_dict', {}))

    if check_item == "multiqueue":
        test.log.info("Checking multiqueue configuration in guest")
        guest_iface_info = vm_session.cmd_output("ip --color=never l").strip()
        iface_name = re.findall(
            r"^\d+: (\S+?)[@:].*state UP.*$", guest_iface_info, re.MULTILINE)[0]
        if not iface_name:
            test.fail("Failed to get network interface name in guest")

        _, output = vm_session.cmd_status_output("ethtool -l %s" % iface_name)
        test.log.debug("ethtool cmd output:%s" % output)
        if not re.findall("Combined:.*?%s" % expected_value, output):
            test.fail("Expected Current hardware settings Combined: %d" % expected_value)

    elif check_item == "mtu":
        vm_iface = interface_base.get_vm_iface(vm_session)
        vm_iface_info = utils_net.get_linux_iface_info(vm_iface, session=vm_session)
        vm_mtu = vm_iface_info['mtu']
        if int(vm_mtu) != int(expected_value):
            test.fail(f'MTU of interface inside vm should be '
                      f'{expected_value}, not {vm_mtu}')
        test.log.debug('MTU check inside vm PASS')

    elif check_item == "qos":
        check_dev = params.get("check_dev")
        if params.get("iface_type") == "direct":
            key_class = 'outbound'
            key_filter = 'inbound'
        else:
            key_class = 'inbound'
            key_filter = 'outbound'

        if not utils_net.check_class_rules(
                check_dev, '1:1', iface_dict['bandwidth'][key_class]):
            test.fail('Class rule check failed')
        if not utils_net.check_filter_rules(
                check_dev, iface_dict['bandwidth'][key_filter]):
            test.fail('Filter rule check failed')
        test.log.debug('QOS check for vm PASS')

    vm_session.close()


def run(test, params, env):
    """
    Test the lifecycle of VM with different types of interface.

    Test steps:
    1. Add the interface configuration.
    2. Try to start the VM
    3. Check the VM network function:
       a) Verify interface configuration in live XML
       b) Login VM to check the connectivity
       c) Check the multiqueue (if there is)
       d) Check the MTU (if there is)
       e) Check the QoS setting (if there is)
    4-8 Check guest lifecycle
    """
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    iface_type = params.get('iface_type')
    driver_queues = params.get("driver_queues")
    mtu = params.get("mtu")
    ping_target = params.get('ping_target', 'www.google.com')
    vm_attrs = eval(params.get("vm_attrs", "{}"))

    if not utils_misc.wait_for(
            lambda: utils_net.get_default_gateway(iface_name=True, force_dhcp=True, json=True) is not None, timeout=15):
        test.log.error("Cannot get default gateway in 15s")
    host_iface = utils_net.get_default_gateway(iface_name=True, force_dhcp=True, json=True).split()[0]
    params["host_iface"] = host_iface
    iface_dict = params.get('iface_dict', "{}")
    xpath_checks_str = params.get('xpath_checks', '')
    if iface_type == "direct":
        xpath_checks_str = params.get('xpath_checks') % host_iface
        params.update({"iface_dict": iface_dict % host_iface})

    try:
        test.log.debug("TEST_SETUP: Prepare before using interface")
        network_base.preparation_for_iface(iface_type, params)

        test.log.debug("TEST_STEP 1: Add interface configuration to VM")
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        vmxml.setup_attrs(**vm_attrs)
        libvirt_vmxml.modify_vm_device(
            vm_xml.VMXML.new_from_inactive_dumpxml(vm_name), 'interface', eval(params.get("iface_dict")))
        test.log.debug("The test guest xml is:%s", vm_xml.VMXML.new_from_inactive_dumpxml(vm_name))

        test.log.debug("TEST_STEP 2: Start vm")
        virsh.start(vm_name, **VIRSH_ARGS)

        test.log.debug("TEST_STEP 3: Check VM xml and connectivity")
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        xpath_list = [{'element_attrs': [check.strip()]} for check in xpath_checks_str.split(',') if check.strip()]
        libvirt_vmxml.check_guest_xml_by_xpaths(vmxml, xpath_list)
        test.log.debug("Interface configuration verified in live XML")

        ips = [ping_target]
        vm.create_serial_console()
        session = vm.wait_for_serial_login(timeout=240)
        network_base.ping_check(params, ips, session)
        session.close()

        check_guest_internal_value(test, vm, "multiqueue", driver_queues, params)
        if iface_type != "direct":
            check_guest_internal_value(test, vm, "mtu", mtu, params)
        check_guest_internal_value(test, vm, "qos", '', params)

        test.log.debug("TEST_STEP 4: Reboot VM in guest os")
        if vm.serial_console is None:
            vm.create_serial_console()
        session = vm.wait_for_serial_login(timeout=240)
        session.sendline("reboot")
        session.close()
        vm.wait_for_serial_login(timeout=240)

        test.log.debug("TEST_STEP 5: Destroy and start the VM")
        virsh.destroy(vm_name, **VIRSH_ARGS)
        virsh.start(vm_name, **VIRSH_ARGS)

        test.log.debug("TEST_STEP 6: Save and restore VM")
        save_path = os.path.join(data_dir.get_tmp_dir(), f"{vm_name}.save")
        virsh.save(vm_name, save_path, **VIRSH_ARGS)
        virsh.restore(save_path, **VIRSH_ARGS)
        if os.path.exists(save_path):
            os.remove(save_path)

        test.log.debug("TEST_STEP 7: Suspend and resume VM")
        virsh.suspend(vm_name, **VIRSH_ARGS)
        virsh.resume(vm_name, **VIRSH_ARGS)

        test.log.debug("TEST_STEP 8: Reboot VM with virsh cmd")
        virsh.reboot(vm_name, **VIRSH_ARGS)

    finally:
        test.log.debug("TEST_TEARDOWN: Restoring VM configuration")
        virsh.destroy(vm_name)
        vmxml_backup.sync()
        network_base.cleanup_for_iface(iface_type, params)
