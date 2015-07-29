import os
import logging
import re
import subprocess

from autotest.client import utils
from autotest.client.shared import error
from virttest import virsh, utils_test, aexpect, utils_passthrough
from virttest.libvirt_xml.vm_xml import VMXML
from virttest.libvirt_xml.nodedev_xml import NodedevXML
from virttest.libvirt_xml import base, xcepts, accessors
from virttest.staging import service


def run(test, params, env):
    """
    Test for USB device passthrough to libvirt guest.
        1. Get params.
        2. Store the result of 'lsusb' on guest.
        3. Passthrough USB device to guest.
        4. Start guest and get the result of 'lsusb' on guest.
        5. Compare the result of 'lsusb' before and after
            passthrough of USB device to guest.
    """

    def find_usbs():
        lsusb_op = session.cmd_output("lsusb")
        lsusb_lines = lsusb_op.splitlines()
        usb_list = []
        for i in lsusb_lines:
            usb_list.append(re.sub(r' ', ' ', i.split(' ')[5]))
        return usb_list

    def is_USB(device_type):
        usbs = utils.run("lsusb", timeout=10,
                         ignore_status='False', verbose=False)
        list_usb = (usbs.stdout.strip()).splitlines()
        for usb in list_usb:
            usb_listed = (usb.split(' '))[5]
            if device_type == usb_listed:
                return True
        return False

    device_type = params.get("usb_dev_label", 'all')
    # Check the parameters from configuration file.
    # pass_adapter would capture those card details
    # where passthrough test will takes place
    vm = env.get_vm(params["main_vm"])
    if not vm.is_alive():
        vm.start()
    timeout = float(params.get("login_timeout", 240))
    session = vm.wait_for_login(timeout=timeout)
    lsusb_op_bef = find_usbs()
    existing_usb = utils.run(
        "lsusb", timeout=10, ignore_status='False', verbose=False)
    list_usb = (existing_usb.stdout.strip()).splitlines()
    if device_type == 'all':
        pass_usbs = []
        for usb in list_usb:
            if ((usb.split(' '))[5]) in lsusb_op_bef:
                logging.info(
                    "%s already inuse within guest.. skipping", ((usb.split(' '))[5]))
                continue
            pass_usbs.append(usb)
        if pass_usbs == '':
            raise error.TestNAError("No USB device found.")
    elif re.match(r'\w{4}:\w{4}', device_type):
        pass_usbs = []
        if not is_USB(device_type):
            raise error.TestNAError("NOT_A_USB")
        for usb in list_usb:
            if (((usb.split(' '))[5]) == device_type):
                if ((usb.split(' '))[5]) in lsusb_op_bef:
                    logging.info(
                        "%s already inuse within guest.. skipping", usb)
                else:
                    pass_usbs.append(usb)
    elif 'ENTER' in device_type:
        raise error.TestNAError("Please enter your device name for test.")
    else:
        raise error.TestNAError("Please enter proper value")
    if pass_usbs == []:
        raise error.TestNAError(
            "Either there are no usbs  available for guest passthrough or already in use within guest.. skipping..")
    logging.info(
        "Passthrough will occur for following USB devices : %s", pass_usbs)
    pass_reported = []
    fail_reported = []
    failures_reported = 0
    for usb_dev in pass_usbs:
        logging.info("Passthrough started for usb device %s" % str(usb_dev))
        # Take backup of guest xml
        vmxml = VMXML.new_from_inactive_dumpxml(params.get("main_vm"))
        backup_xml = vmxml.copy()
        if not vm.is_alive():
            vm.start()
        logging.info(
            "USB devices within guest before passthrough: %s", lsusb_op_bef)
        # Edit guest xml to add hostdev entries for diffrent ports
        usb_address = {'bus': '0x' + (usb_dev.split(' '))[1], 'device': '0x' + ((usb_dev.split(' '))[3]).strip(':'), 'vendor_id': '0x' + (
            ((usb_dev.split(' '))[5]).split(":"))[0], 'product_id': '0x' + (((usb_dev.split(' '))[5]).split(":"))[1]}
        vmxml.add_hostdev(usb_address, 'subsystem', 'usb', 'yes')
        # Start the guest after passthrough compare pci/modules/device
        # details with before passthrough
        try:
            vmxml.sync()
            vm.start()
            timeout = float(params.get("login_timeout", 240))
            session = vm.wait_for_login(timeout=timeout)
            lsusb_op_aft = find_usbs()
            logging.info(
                "USB devices within guest after passthrough: %s" % str(lsusb_op_aft))
            if lsusb_op_bef == lsusb_op_aft:
                failures_reported = 1
                logging.info("Passthrough failed for USB device %s" %
                             str(usb_dev))
                fail_reported.append(usb_dev)
            else:
                logging.info("Passthrough passed for USB device %s" %
                             str(usb_dev))
                pass_reported.append(usb_dev)
        finally:
            backup_xml.sync()
    logging.info("Summary of USB device passthrough test: ")
    logging.info("Passthrough failed for USB devices %s", fail_reported)
    logging.info("Passthrough passed for USB devices %s", pass_reported)
    if failures_reported:
        raise error.TestFail(
            "USB device passthrough failed for one or more devices, see above output for more details")
