# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0
#   Author: Nannan Li<nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

import logging
import os
import re
import time

from virttest import data_dir
from virttest import utils_misc
from virttest import utils_net
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices import interface
from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_vmxml
from provider.interface import interface_base

from provider.virtual_network import network_base

VIRSH_ARGS = {'debug': True, 'ignore_status': False}

def check_guest_internal_value(test, vm, check_item, expected_value, **kwargs):
    """
    Check guest internal configuration values

    :param test: test instance
    :param vm: VM instance
    :param check_item: type of check (multiqueue, mtu, qos)
    :param expected_value: expected value to verify
    :param kwargs: additional parameters like iface_dict, bridge_name
    """
    vm_session = vm.wait_for_login()
    iface_dict = kwargs.get('iface_dict', {})

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
        bandwidth = iface_dict['bandwidth']
        bridge_name = kwargs.get('bridge_name', 'virbr0')
        if 'inbound' in bandwidth:
            utils_net.check_class_rules(bridge_name, "1:1", bandwidth['inbound'])
        if 'outbound' in bandwidth:
            utils_net.check_class_rules(bridge_name, "1:2", bandwidth['outbound'])

    vm_session.close()


def run(test, params, env):
    """
    Test the lifecycle of VM with different types of interface using iface_dict

    Test steps:
    1. Add the interface configuration (using iface_dict) into an existing VM's XML
    2. Try to start the VM
    3. Check the VM network function:
       a) Verify interface configuration in live XML
       b) Login VM to check the connectivity
       c) Check the multiqueue (if there is)
       d) Check the MTU (if there is)
       e) Check the QoS setting (if there is)
    4. Destroy and start the VM
    5. Reboot the VM by virsh cmd
    6. Save and restore
    7. Suspend and resume
    8. Reboot the VM in guest OS
    """
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    iface_type = params.get('iface_type')
    iface_dict = eval(params.get('iface_dict', "{}"))
    driver_queues = params.get("driver_queues")
    mtu = params.get("mtu")
    ping_target = params.get('ping_target', 'www.google.com')
    xpath_checks_str = params.get('xpath_checks', '')
    vm_attrs = eval(params.get("vm_attrs", "{}"))

    if not utils_misc.wait_for(
            lambda: utils_net.get_default_gateway(iface_name=True, force_dhcp=True, json=True) is not None, timeout=15):
        logging.error("Cannot get default gateway in 15s")
    host_iface = utils_net.get_default_gateway(iface_name=True, force_dhcp=True, json=True).split()[0]
    params["host_iface"] = host_iface

    try:
        test.log.debug("TEST_SETUP: Prepare before using interface")
        network_base.preparation_for_iface(iface_type, params)

        test.log.debug("TEST_STEP 1: Add interface configuration to VM")
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        vmxml.setup_attrs(**vm_attrs)
        libvirt_vmxml.modify_vm_device(
            vm_xml.VMXML.new_from_inactive_dumpxml(vm_name), 'interface', iface_dict)

        test.log.debug("TEST_STEP 2: Start vm")
        virsh.start(vm_name, **VIRSH_ARGS)

        test.log.debug("TEST_STEP 3: Check VM xml and connectivity")
        libvirt_vmxml.check_guest_xml_by_xpaths(
            vm_name, [check.strip() for check in xpath_checks_str.split(',')])
        test.log.debug("Interface configuration verified in live XML")
        ips = [ping_target]
        session = vm.wait_for_login(timeout=240)
        network_base.ping_check(params, ips, session)
        session.close()
        check_guest_internal_value(test, vm, "multiqueue", driver_queues, **params)
        check_guest_internal_value(test, vm, "mtu", mtu, **params)
        check_guest_internal_value(test, vm, "qos", '', **params)

        test.log.debug("TEST_STEP 4: Destroy and start the VM")
        virsh.destroy(vm_name, **VIRSH_ARGS)
        virsh.start(vm_name, **VIRSH_ARGS)

        test.log.debug("TEST_STEP 5: Reboot VM by virsh command")
        virsh.reboot(vm_name, **VIRSH_ARGS)

        test.log.debug("TEST_STEP 6: Save and restore VM")
        save_path = os.path.join(data_dir.get_tmp_dir(), f"{vm_name}.save")
        virsh.save(vm_name, save_path, **VIRSH_ARGS)
        virsh.restore(save_path, **VIRSH_ARGS)
        vm.wait_for_login(timeout=240)
        if os.path.exists(save_path):
            os.remove(save_path)

        test.log.debug("TEST_STEP 7: Suspend and resume VM")
        virsh.suspend(vm_name, **VIRSH_ARGS)
        virsh.resume(vm_name, **VIRSH_ARGS)

        test.log.debug("TEST_STEP 8: Reboot VM in guest OS")
        virsh.reboot(vm_name, **VIRSH_ARGS)
        vm.wait_for_login(timeout=240).close()

        test.log.debug("TEST_STEP 9: Reboot VM in guest OS")
        session = vm.wait_for_login(timeout=240)
        session.cmd("reboot", ignore_all_errors=True)
        session.close()


    finally:
        test.log.debug("TEST_CLEANUP: Restoring VM configuration")
        if vm.is_alive():
            vm.destroy()
        vmxml_backup.sync()
        network_base.cleanup_for_iface(iface_type, params)
