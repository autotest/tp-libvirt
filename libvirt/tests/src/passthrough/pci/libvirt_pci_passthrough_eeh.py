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
# Author: Tasmiya.Nalatwad<tasmiya@linux.vnet.ibm.com>
# EEH Test on pci passthrough devices


import logging as log
import aexpect
import platform
import time

from avocado.utils import process

from virttest import virsh
from virttest.libvirt_xml.vm_xml import VMXML
from virttest.libvirt_xml.nodedev_xml import NodedevXML
from virttest.test_setup import PciAssignable
from virttest import utils_test, utils_misc
from virttest.libvirt_xml.devices.controller import Controller
from virttest import utils_package
from virttest import libvirt_version

# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test EEH functionality on PCI Passthrough device
    of a libvirt guest

    a). NIC:
        1. Get params.
        2. Get the pci device function.
        3. Start guest
        4. prepare device xml to be attached
        5. hotplug the device
        6. check device hotplugged or not
        7. Ping to server_ip from guest
        8. Get location code for the pci device
        9. Trigger EEH on pci device
        10. Check EEH recovery on the device
        11. check device availability inside guest
    b). STORAGE:
        1. Get params.
        2. Get the pci device function.
        3. Start guest
        4. prepare device xml to be attached
        5. hotplug the device
        6. check device hotplugged or not
        7. check STORAGE device inside guest
        8. Get location code for the pci device
        9. mount file (/dev/nvme*) on /mnt
        9. Trigger EEH on pci device
        10. Check EEH recovery on the device
        11. check device availability inside guest
    """
    # get the params from params
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    device_name = params.get("libvirt_pci_net_dev_name", "ENTER_YOUR.DEV.NAME")
    device_type = params.get("libvirt_pci_device_type", "NIC")
    pci_id = params.get("libvirt_pci_net_dev_label", "ENTER_YOUR.DEV.LABEL")
    net_ip = params.get("libvirt_pci_net_ip", "ENTER_YOUR.IP")
    server_ip = params.get("libvirt_pci_server_ip",
                           "ENTER_YOUR.SERVER.IP")
    netmask = params.get("libvirt_pci_net_mask", "ENTER_YOUR.MASK")
    timeout = int(params.get("timeout", "ENTER_YOUR.TIMEOUT.VALUE"))
    dargs = {'debug': True, 'ignore_status': True}

    vmxml = VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()
    devices = vmxml.get_devices()
    pci_devs = []

    cntlr_index = params.get("index", "1")
    cntlr_model = params.get("model", "pci-root")
    cntlr_type = "pci"

    controllers = vmxml.get_controllers(cntlr_type, cntlr_model)
    index_list = []
    for controller in controllers:
        index_value = controller.get("index")
        if index_value is not None:
            index_list.append(int(index_value))
    if index_list:
        next_index = max(index_list) + 1
    else:
        next_index = int(cntlr_index)

    controller = Controller("controller")
    controller.type = cntlr_type
    controller.index = str(next_index)
    controller.model = cntlr_model

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

    output = session.cmd_output("ip link")
    logging.debug("checking for output - %s", output)
    nic_list_before = str(output.splitlines())
    logging.debug("nic_list before hotplug %s", nic_list_before)
    obj = PciAssignable()
    # get all functions id's
    print("Tasmiya PCI_ID : %s" % pci_id)
    pci_ids = obj.get_same_group_devs(pci_id)
    for val in pci_ids:
        temp = val.replace(":", "_")
        pci_devs.extend(["pci_"+temp])
    pci_val = pci_devs[0].replace(".", "_")
    if device_type == "NIC":
        if not vm.is_alive():
            vm.start()
        session = vm.wait_for_login()
        nic_list_before = vm.get_pci_devices()
    pci_xml = NodedevXML.new_from_dumpxml(pci_val)
    pci_address = pci_xml.cap.get_address_dict()
    dev = VMXML.get_device_class('hostdev')()
    dev.mode = 'subsystem'
    dev.type = 'pci'
    dev.managed = 'no'
    dev.source = dev.new_source(**pci_address)
    arch = platform.machine()
    ioa_system_info = params.get("ioa_system_info", "ioa-bus-error")
    func = params.get("function", "6")
    max_freeze = params.get("max_freeze", "5")
    eeh_guest = params.get("eeh_guest", "no")
    eeh_host = params.get("eeh_host", "no")

    def test_ping():
        try:
            output = session.cmd_output('lspci -nn | grep "%s"' % device_name)
            nic_id = str(output).split(' ', 1)[0]
            nic_name = str(utils_misc.get_interface_from_pci_id(nic_id,
                                                                session))
            session.cmd("ip addr flush dev %s" % nic_name)
            session.cmd("ip addr add %s/%s dev %s"
                        % (net_ip, netmask, nic_name))
            session.cmd("ip link set %s up" % nic_name)
            s_ping, o_ping = utils_test.ping(server_ip, count=5,
                                             interface=net_ip, timeout=30,
                                             session=session)
            logging.info(s_ping)
            logging.info(o_ping)
            if s_ping:
                test.fail("Ping test failed")
        except aexpect.ShellError as detail:
            test.error("Succeed to set ip on guest, but failed "
                       "to bring up interface.\n"
                       "Detail: %s." % detail)

    def detach_device(pci_devs, pci_ids):
        # detaching the device from host
        for pci_value, pci_node in zip(pci_devs, pci_ids):
            pci_value = pci_value.replace(".", "_")
            cmd = "lspci -ks %s | grep 'Kernel driver in use' |\
                   awk '{print $5}'" % pci_node
            driver_name = process.run(cmd, shell=True).stdout_text.strip()
            if driver_name == "vfio-pci":
                logging.debug("device already detached")
            else:
                if virsh.nodedev_detach(pci_value).exit_status:
                    test.error("Hostdev node detach failed")
                driver_name = process.run(cmd, shell=True).stdout_text.strip()
                if driver_name != "vfio-pci":
                    test.error("driver bind failed after detach")

    def reattach_device(pci_devs, pci_ids):
        # reattach the device to host
        for pci_value, pci_node in zip(pci_devs, pci_ids):
            pci_value = pci_value.replace(".", "_")
            cmd = "lspci -ks %s | grep 'Kernel driver in use' |\
                   awk '{print $5}'" % pci_node
            driver_name = process.run(cmd, shell=True).stdout_text.strip()
            if driver_name != "vfio-pci":
                logging.debug("device already attached")
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
        if arch == "ppc64le":
            if libvirt_version.version_compare(3, 10, 0):
                detach_device(pci_devs, pci_ids)
        else:
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

    def check_device():
        # Get the result of "fdisk -l" in guest, and
        # compare the result with fdisk_list_before.
        output = session.cmd_output("fdisk -l|grep \"Disk identifier:\"")
        fdisk_list_after = output.splitlines()
        if fdisk_list_after == fdisk_list_before:
            test.fail("Didn't find the disk attached to guest.")

    def get_new_dmesg_logs(last_dmesg_line):
        """
        Fetch new `dmesg` logs since the last pointer.
        """
        cmd = "dmesg"
        res_status, res_output = session.cmd_status_output(cmd)
        if res_status != 0:
            test.fail(f"Failed to fetch dmesg logs, status: {res_status}, output: {res_output}")
        logs = res_output.splitlines()
        if last_dmesg_line is not None:
            # Get logs after the last known line
            try:
                idx = logs.index(last_dmesg_line)
                new_logs = logs[idx + 1:]
            except ValueError:
                new_logs = logs
        else:
            new_logs = logs
        return new_logs, logs[-1] if logs else None

    def test_eeh_nic():
        cmd = f"echo {max_freeze} > /sys/kernel/debug/powerpc/eeh_max_freezes"
        process.run(cmd, shell=True)
        loc_code = str(utils_misc.get_location_code(pci_id))
        num_of_miss = 0
        last_dmesg_line = None  # Initialize the last dmesg line pointer
        pass_hit = 0
        for num_of_hit in range(int(max_freeze)):
            if num_of_miss < 4:
                # Inject EEH error using below command
                eeh_cmd = f"errinjct {ioa_system_info} -f {func} -p {loc_code} -m 0"
                if eeh_guest == "yes":
                    session.cmd(eeh_cmd)
                if eeh_host == "yes":
                    process.run(eeh_cmd, shell=True)
                is_hit, last_dmesg_line = check_eeh_hit(last_dmesg_line)
                if not is_hit:
                    num_of_miss += 1
                    if num_of_hit >= 1 and pass_hit != 0:
                        test.fail(f"Failed to inject EEH after {pass_hit} sucessfull attempt for {pci_ids}. Please check dmesg logs")
                    logging.debug(f"PCI Device {pci_ids} EEH hit failed")
                    continue
                is_recovered, last_dmesg_line = check_eeh_pci_device_recovery(last_dmesg_line)
                if not is_recovered:
                    test.fail(f"PCI device {pci_ids} recovery failed after {num_of_hit} EEH")
            else:
                test.fail("Failed to Inject EEH for 5 times")
            pass_hit += 1
        is_removed, last_dmesg_line = check_eeh_removed(last_dmesg_line)
        if is_removed:
            logging.debug(f"PCI Device {pci_ids} removed successfully")
        else:
            test.fail(f"PCI Device {pci_ids} failed to permanetly disable after max hit")

    def check_eeh_pci_device_recovery(last_dmesg_line):
        """
        Check if the pci device is recovered successfully after injecting EEH
        """
        tries = 60
        for _ in range(0, tries):
            logs, last_dmesg_line = get_new_dmesg_logs(last_dmesg_line)
            if any('permanent failure' in log for log in logs):
                logging.debug("TEST WILL FAIL AS PERMANENT FAILURE IS SEEN")
            elif any('EEH: Recovery successful.' in log for log in logs):
                logging.debug("waiting for pci device to recover %s", pci_ids)
                break
            time.sleep(5)
        else:
            logging.debug("EEH recovery failed for pci device %s" % pci_ids)
        tries = 30
        for _ in range(0, tries):
            if sorted(vm.get_pci_devices()) != sorted(nic_list_before):
                logging.debug("Adapter found after EEH was injection successfully")
                return True, last_dmesg_line
            time.sleep(1)
        return False, last_dmesg_line

    def check_eeh_hit(last_dmesg_line):
        """
        Function to check if EEH is successfully hit
        """
        tries = 30
        for _ in range(0, tries):
            logs, last_dmesg_line = get_new_dmesg_logs(last_dmesg_line)
            if any('EEH: Frozen' in log for log in logs):
                return True, last_dmesg_line
            time.sleep(1)
        return False, last_dmesg_line

    def check_eeh_removed(last_dmesg_line):
        """
        Function to check if PCI PT is recovered successfully
        """
        tries = 30
        for _ in range(0, tries):
            cmd = "dmesg"
            res_status, res_output = session.cmd_status_output(cmd)
            if 'permanent failure' in res_output and res_status == 0:
                time.sleep(10)
                return True, last_dmesg_line
            time.sleep(1)
        return False, last_dmesg_line

    try:
        device_hotplug()
        if device_type == "NIC":
            test_ping()
            test_eeh_nic()
        if device_type == "STORAGE":
            check_device()
            test_eeh_storage()
        device_hotunplug()

    finally:
        cmd = "dmesg"
        res_output = session.cmd_output(cmd)
        logging.debug("complete dmesg Logs:: %s", res_output)
        backup_xml.sync()
        if session:
            session.close()
        if arch == "ppc64le":
            if libvirt_version.version_compare(3, 10, 0):
                pci_devs.sort()
                reattach_device(pci_devs, pci_ids)
        else:
            if not libvirt_version.version_compare(3, 10, 0):
                pci_devs.sort()
                reattach_device(pci_devs, pci_ids)
