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
# See LICENSE for more details.
#
# Copyright: 2017 IBM
# Author: Prudhvi Miryala<mprudhvi@linux.vnet.ibm.com>
# Host device hotplug test


import os
import logging
import aexpect
import time
import re
from avocado.utils import process
from virttest import virsh
from virttest.libvirt_xml.vm_xml import VMXML
from virttest.libvirt_xml.nodedev_xml import NodedevXML
from virttest.test_setup import PciAssignable
from virttest import utils_misc
from virttest import data_dir
from virttest.libvirt_xml.devices.controller import Controller
from virttest import utils_package
from provider import libvirt_version
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
        10. test virsh dump
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
    stress = params.get("stress_val", "ENTER_YOUR.STRESS.VALUE")
    timeout = int(params.get("timeout", "ENTER_YOUR.TIMEOUT.VALUE"))
    # Check the parameters from configuration file.
    for each_param in params.itervalues():
        if "ENTER_YOUR" in each_param:
            test.cancel("Please enter the configuration details of %s."
                        % each_param)
    vmxml = VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()
    devices = vmxml.get_devices()
    nic_id = None
    pci_value = None
    pci_devs = []
    controller = Controller("controller")
    controller.type = "pci"
    controller.index = params.get("index", "1")
    controller.model = params.get("model", "pci-root")
    devices.append(controller)
    vmxml.set_devices(devices)
    vmxml.sync()
    save_file = os.path.join(data_dir.get_tmp_dir(), "attach.xml")
    try:
        fp = open(save_file, "w")
    except IOError:
        logging.debug("Error: File does not appear to exist.")
    if not vm.is_alive():
        vm.start()
        session = vm.wait_for_login()
    if not utils_package.package_install(["ppc64-diag",
                                          "librtas", "powerpc-utils"],
                                         session, 360):
        test.error('Fail on dependencies installing ')
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
    dev.hostdev_type = 'pci'
    dev.managed = 'no'
    dev.source = dev.new_source(**pci_address)
    dev = str(dev)
    temp, dev = dev.split("?>", 1)
    fp.write(dev)
    fp.close()

    def detach_device(pci_devs, pci_ids):
        # detaching the device from host
        for pci_value, pci_node in map(None, pci_devs, pci_ids):
            pci_value = pci_value.replace(".", "_")
            cmd = "lspci -ks %s | grep 'Kernel driver in use' |\
                   awk '{print $5}'" % pci_node
            driver_name = process.system_output(cmd, shell=True).strip()
            if driver_name == "vfio-pci":
                logging.debug("device alreay detached")
            else:
                if virsh.nodedev_detach(pci_value).exit_status:
                    test.error("Hostdev node detach failed")
                driver_name = process.system_output(cmd, shell=True).strip()
                if driver_name != "vfio-pci":
                    test.error("driver bind failed after detach")

    def reattach_device(pci_devs, pci_ids):
        # reattach the device to host
        for pci_value, pci_node in map(None, pci_devs, pci_ids):
            pci_value = pci_value.replace(".", "_")
            cmd = "lspci -ks %s | grep 'Kernel driver in use' |\
                   awk '{print $5}'" % pci_node
            driver_name = process.system_output(cmd, shell=True).strip()
            if driver_name != "vfio-pci":
                logging.debug("device alreay attached")
            else:
                if virsh.nodedev_reattach(pci_value).exit_status:
                    test.fail("Hostdev node reattach failed")
                driver_name = process.system_output(cmd, shell=True).strip()
                if driver_name == "vfio-pci":
                    test.error("driver bind failed after reattach")

    def check_attach_pci():
        session = vm.wait_for_login()
        output = session.cmd_output("ip link")
        nic_list_after = str(output.splitlines())
        if nic_list_after != nic_list_before:
            return True
        else:
            return False

    def device_hotplug():
        if not libvirt_version.version_compare(3, 10, 0):
            detach_device(pci_devs, pci_ids)
        # attach the device in hotplug mode
        result = virsh.attach_device(vm_name, save_file,
                                     flagstr="--live", debug=True)
        if result.exit_status:
            test.error(result.stdout)
        else:
            logging.debug(result.stdout)
        if not utils_misc.wait_for(check_attach_pci, timeout):
            test.fail("timeout value is not sufficient")

    # detach hot plugged device
    def device_hotunplug():
        result = virsh.detach_device(vm_name, save_file,
                                     flagstr="--live", debug=True)
        if result.exit_status:
            test.fail(result.stdout)
        else:
            logging.debug(result.stdout)
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
            cmd = "ping -I %s %s -c 5" % (net_ip, server_ip)
            output = session.cmd_output(cmd, timeout=10)
            # Test Flood Ping
            cmd = "ping -I %s %s -c 1000 -f" % (net_ip, server_ip)
            output = session.cmd_output(cmd, timeout=100)
            logging.debug(output)
        except aexpect.ShellError, detail:
            test.error("Succeed to set ip on guest, but failed "
                       "to ping server ip from guest.\n"
                       "Detail: %s." % detail)

    def test_life_cycle():
        # Suspend
        result = virsh.suspend(vm_name, ignore_status=True, debug=True)
        libvirt.check_exit_status(result)
        cmd = "virsh domstate %s" % vm_name
        state = process.system_output(cmd, shell=True, verbose=True)
        if not re.search("paused", state):
            test.fail("suspend vm failed")
        # Resume
        result = virsh.resume(vm_name, ignore_status=True, debug=True)
        libvirt.check_exit_status(result)
        cmd = "virsh domstate %s" % vm_name
        state = process.system_output(cmd, shell=True, verbose=True)
        if not re.search("running", state):
            test.fail("resume vm failed")
        if check_attach_pci():
            logging.debug("adapter found after suspend/resume")
        else:
            test.fail("passthroughed adapter not found after suspend/resume")
        # Reboot(not supported on RHEL6)
        result = virsh.reboot(vm_name, ignore_status=True, debug=True)
        supported_err = 'not supported by the connection driver:\
                         virDomainReboot'
        if supported_err in result.stderr.strip():
            logging.info("Reboot is not supported")
        else:
            libvirt.check_exit_status(result)
        session = vm.wait_for_login()
        if check_attach_pci():
            logging.debug("adapter found after reboot")
        else:
            test.fail("passthroughed adapter not found after reboot")

    def test_dump():
        dump_xml = virsh.dumpxml(vm_name).stdout.strip()
        logging.debug(dump_xml)
    try:
        device_hotplug()
        logging.debug("********* Testing ping and flood ping ************")
        test_ping()
        logging.debug("******* Testing guest life cycle*********")
        test_life_cycle()
        logging.debug("****** Testing Virsh Dump *******")
        test_dump()
        device_hotunplug()
        # test Stress
        for stress_value in range(0, int(stress)):
            device_hotplug()
            device_hotunplug()

    finally:
        backup_xml.sync()
        if not libvirt_version.version_compare(3, 10, 0):
            pci_devs.sort()
            reattach_device(pci_devs, pci_ids)
