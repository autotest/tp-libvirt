#!/usr/bin/env python

# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
#
# Author: Prudhvi Miryala<mprudhvi@linux.vnet.ibm.com>
# Host device hotplug test


import os
import logging
import aexpect
import time

from avocado.utils import process

from virttest import virsh
from virttest.libvirt_xml.vm_xml import VMXML
from virttest.libvirt_xml.nodedev_xml import NodedevXML
from virttest.test_setup import PciAssignable
from virttest import utils_misc
from virttest import data_dir
from virttest.libvirt_xml.devices.controller import Controller
from virttest import utils_package
from virttest import utils_net
from virttest import libvirt_version
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Test for PCI single function device(NIC or Infiniband)
    passthrough to libvirt guest in hotplug mode.

    a). NIC Or Infiniband:
        1. Get params.
        2. Get the pci device function.
        3. Start guest
        4. prepare device xml to be attached
        5. hotplug the device
        6. check device hotplugged or not
        7. Ping to server_ip from guest
        8. test flood ping
        9. test guest life cycle
        10. test virsh dumpxml
        11. hotunplug the device
        12. test stress
           to verify the new network device.
    """
    # get the params from params
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    device_name = params.get("libvirt_pci_net_dev_name", "ENTER_YOUR.DEV.NAME")
    pci_id = params.get("libvirt_pci_net_dev_label", "ENTER_YOUR.DEV.LABEL")
    net_ip = params.get("libvirt_pci_net_ip", "ENTER_YOUR.IP")
    server_ip = params.get("libvirt_pci_server_ip",
                           "ENTER_YOUR.SERVER.IP")
    netmask = params.get("libvirt_pci_net_mask", "ENTER_YOUR.MASK")
    stress_val = params.get("stress_val", "1")
    stress = params.get("stress", "no")
    timeout = int(params.get("timeout", "ENTER_YOUR.TIMEOUT.VALUE"))
    suspend_operation = params.get("suspend_operation", "no")
    reboot_operation = params.get("reboot_operation", "no")
    virsh_dumpxml = params.get("virsh_dumpxml", "no")
    virsh_dump = params.get("virsh_dump", "no")
    flood_ping = params.get("flood_ping", "no")
    # Check the parameters from configuration file.
    for each_param in params.itervalues():
        if "ENTER_YOUR" in each_param:
            test.cancel("Please enter the configuration details of %s."
                        % each_param)
    vmxml = VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()
    devices = vmxml.get_devices()
    pci_devs = []
    dargs = {'debug': True, 'ignore_status': True}
    controller = Controller("controller")
    controller.type = "pci"
    controller.index = params.get("index", "1")
    controller.model = params.get("model", "pci-root")
    devices.append(controller)
    vmxml.set_devices(devices)
    vmxml.sync()
    if not vm.is_alive():
        vm.start()
        session = vm.wait_for_login()
    if not utils_package.package_install(["ppc64-diag",
                                          "librtas", "powerpc-utils"],
                                         session, 360):
        test.cancel('Fail on dependencies installing')
    if virsh_dump == "yes":
        dump_file = os.path.join(data_dir.get_tmp_dir(), "virshdump.xml")
    output = session.cmd_output("ip link")
    logging.debug("checking for output - %s", output)
    nic_list_before = str(output.splitlines())
    logging.debug("nic_list before hotplug %s", nic_list_before)
    obj = PciAssignable()
    # get all functions id's
    pci_ids = obj.get_same_group_devs(pci_id)
    for val in pci_ids:
        temp = val.replace(":", "_")
        pci_devs.extend(["pci_"+temp])
    pci_val = pci_devs[0].replace(".", "_")
    pci_xml = NodedevXML.new_from_dumpxml(pci_val)
    pci_address = pci_xml.cap.get_address_dict()
    dev = VMXML.get_device_class('hostdev')()
    dev.mode = 'subsystem'
    dev.type = 'pci'
    dev.managed = 'no'
    dev.source = dev.new_source(**pci_address)

    def detach_device(pci_devs, pci_ids):
        # detaching the device from host
        for pci_value, pci_node in map(None, pci_devs, pci_ids):
            pci_value = pci_value.replace(".", "_")
            cmd = "lspci -ks %s | grep 'Kernel driver in use' |\
                   awk '{print $5}'" % pci_node
            driver_name = process.run(cmd, shell=True).stdout_text.strip()
            if driver_name == "vfio-pci":
                logging.debug("device alreay detached")
            else:
                if virsh.nodedev_detach(pci_value).exit_status:
                    test.error("Hostdev node detach failed")
                driver_name = process.run(cmd, shell=True).stdout_text.strip()
                if driver_name != "vfio-pci":
                    test.error("driver bind failed after detach")

    def reattach_device(pci_devs, pci_ids):
        # reattach the device to host
        for pci_value, pci_node in map(None, pci_devs, pci_ids):
            pci_value = pci_value.replace(".", "_")
            cmd = "lspci -ks %s | grep 'Kernel driver in use' |\
                   awk '{print $5}'" % pci_node
            driver_name = process.run(cmd, shell=True).stdout_text.strip()
            if driver_name != "vfio-pci":
                logging.debug("device alreay attached")
            else:
                if virsh.nodedev_reattach(pci_value).exit_status:
                    test.fail("Hostdev node reattach failed")
                driver_name = process.run(cmd, shell=True).stdout_text.strip()
                if driver_name == "vfio-pci":
                    test.error("driver bind failed after reattach")

    def check_attach_pci():
        session = vm.wait_for_login()
        output = session.cmd_output("ip link")
        nic_list_after = str(output.splitlines())
        logging.debug(nic_list_after)
        return nic_list_after != nic_list_before

    def device_hotplug():
        if not libvirt_version.version_compare(3, 10, 0):
            detach_device(pci_devs, pci_ids)
        # attach the device in hotplug mode
        result = virsh.attach_device(vm_name, dev.xml,
                                     flagstr="--live", debug=True)
        if result.exit_status:
            test.error(result.stdout.strip())
        else:
            logging.debug(result.stdout.strip())
        if not utils_misc.wait_for(check_attach_pci, timeout):
            test.fail("timeout value is not sufficient")

    # detach hot plugged device
    def device_hotunplug():
        result = virsh.detach_device(vm_name, dev.xml,
                                     flagstr="--live", debug=True)
        if result.exit_status:
            test.fail(result.stdout.strip())
        else:
            logging.debug(result.stdout.strip())
        # Fix me
        # the purpose of waiting here is after detach the device from
        #  guest it need time to perform any other operation on the device
        time.sleep(timeout)
        if not libvirt_version.version_compare(3, 10, 0):
            pci_devs.sort()
            reattach_device(pci_devs, pci_ids)

    def test_ping():
        try:
            output = session.cmd_output("lspci -nn | grep %s" % device_name)
            nic_id = str(output).split(' ', 1)[0]
            nic_name = str(utils_misc.get_interface_from_pci_id(nic_id,
                                                                session))
            session.cmd("ip addr flush dev %s" % nic_name)
            session.cmd("ip addr add %s/%s dev %s"
                        % (net_ip, netmask, nic_name))
            session.cmd("ip link set %s up" % nic_name)
            s_ping, o_ping = utils_net.ping(dest=server_ip, count=5,
                                            interface=net_ip)
            logging.info(s_ping)
            logging.info(o_ping)
            if s_ping:
                test.fail("Ping test failed")
        except aexpect.ShellError as detail:
            test.error("Succeed to set ip on guest, but failed "
                       "to bring up interface.\n"
                       "Detail: %s." % detail)

    def test_flood_ping():
        # Test Flood Ping
        s_ping, o_ping = utils_net.ping(dest=server_ip, count=5,
                                        interface=net_ip, flood=True)
        logging.info(s_ping)
        logging.info(o_ping)
        if s_ping:
            test.fail("Flood ping test failed")

    def test_suspend():
        # Suspend
        result = virsh.suspend(vm_name, ignore_status=True, debug=True)
        libvirt.check_exit_status(result)
        cmd = "virsh domstate %s" % vm_name
        if "paused" not in virsh.domstate(vm_name, **dargs).stdout:
            test.fail("suspend vm failed")
        # Resume
        result = virsh.resume(vm_name, ignore_status=True, debug=True)
        libvirt.check_exit_status(result)
        if "running" not in virsh.domstate(vm_name, **dargs).stdout:
            test.fail("resume vm failed")
        if check_attach_pci():
            logging.debug("adapter found after suspend/resume")
        else:
            test.fail("passthroughed adapter not found after suspend/resume")

    def test_reboot():
        # Reboot(not supported on RHEL6)
        result = virsh.reboot(vm_name, ignore_status=True, debug=True)
        supported_err = 'not supported by the connection driver:\
                         virDomainReboot'
        if supported_err in result.stderr.strip():
            logging.info("Reboot is not supported")
        else:
            libvirt.check_exit_status(result)
        vm.wait_for_login()
        if check_attach_pci():
            logging.debug("adapter found after reboot")
        else:
            test.fail("passthroughed adapter not found after reboot")

    def test_dumpxml():
        # test dumpxml
        cmd_result = virsh.dumpxml(vm_name, debug=True)
        if cmd_result.exit_status:
            test.fail("Failed to dump xml of domain %s" % vm_name)

    def test_dump():
        # test virsh_dump
        cmd_result = virsh.dump(vm_name, dump_file,
                                option="--memory-only", **dargs)
        if cmd_result.exit_status:
            test.fail("Failed to virsh dump of domain %s" % vm_name)

    try:
        for stress_value in range(0, int(stress_val)):
            device_hotplug()
            test_ping()
            if flood_ping == "yes":
                test_flood_ping()
            if suspend_operation == "yes":
                test_suspend()
            if reboot_operation == "yes":
                test_reboot()
            if virsh_dumpxml == "yes":
                test_dumpxml()
            if virsh_dump == "yes":
                test_dump()
            device_hotunplug()

    finally:
        # clean up
        data_dir.clean_tmp_files()
        backup_xml.sync()
        if not libvirt_version.version_compare(3, 10, 0):
            pci_devs.sort()
            reattach_device(pci_devs, pci_ids)
