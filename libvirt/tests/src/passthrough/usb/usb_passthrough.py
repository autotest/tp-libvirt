import logging
import re

from avocado.utils import process

from virttest.libvirt_xml.vm_xml import VMXML


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
    def return_usbs_in_guest():
        """
        This function returns the usb devices found
        on guest system as a list variable
        """
        timeout = float(params.get("login_timeout", 240))
        session = vm.wait_for_login(timeout=timeout)
        if (session.cmd_status("lsusb")):
            session.close()
            test.cancel("SKIP:lsusb command has errored out,"
                        "please fix any issues with lsusb command"
                        " on guest")
        usb_list = session.cmd_output("lsusb|awk '{print $6}'")
        session.close()
        return (usb_list)

    def return_usbs_on_host():
        """
        This function returns the usb devices found
        on host system as a list variable
        """
        existing_usb = process.run(
            "lsusb", timeout=10, ignore_status='True', verbose=False, shell=True)
        if existing_usb.exit_status != 0:
            test.cancel("SKIP: lsusb command has errored out,"
                        "please fix any issues with lsusb command"
                        " on host")
        return ((existing_usb.stdout_text.strip()).splitlines())

    device_type = params.get("usb_dev_label", 'all')
    # Check the parameters from configuration file.
    # pass_adapter would capture those card details
    # where passthrough test will takes place
    vm = env.get_vm(params["main_vm"])
    if not vm.is_alive():
        vm.start()
    lsusb_op_bef = return_usbs_in_guest()
    existing_usbs = return_usbs_on_host()
    if device_type == 'all':
        pass_usbs = []
        for usb in existing_usbs:
            if ((usb.split())[5]) in lsusb_op_bef:
                logging.info("%s already inuse within guest so skipping this"
                             "usb device from passthrough test",
                             ((usb.split())[5]))
                continue
            pass_usbs.append(usb)
        if pass_usbs == '':
            test.cancel("No USB device found.")
    elif re.match(r'\w{4}:\w{4}', device_type):
        pass_usbs = []
        if not ([usb for usb in existing_usbs if device_type in usb]):
            test.cancel("Device passed is not a USB device")
        for usb in existing_usbs:
            if (((usb.split())[5]) == device_type):
                if ((usb.split())[5]) in lsusb_op_bef:
                    logging.info(
                        "%s inuse within guest,skipping this device", usb)
                else:
                    pass_usbs.append(usb)
    elif 'ENTER' in device_type:
        test.cancel("Please enter your device name for test.")
    else:
        test.cancel("Please enter proper value for device name")
    if pass_usbs == []:
        test.cancel(
            "No usb devices available or already in use within guest")
    logging.info(
        "Passthrough will occur for following USB devices : %s", pass_usbs)
    pass_reported = []
    fail_reported = []
    failures_reported = 0
    for usb_dev in pass_usbs:
        logging.info("Passthrough started for usb device %s", usb_dev)
        # Take backup of guest xml
        vmxml = VMXML.new_from_inactive_dumpxml(params.get("main_vm"))
        backup_xml = vmxml.copy()
        logging.info(
            "USB devices within guest before passthrough: %s", lsusb_op_bef)
        # Edit guest xml to add hostdev entries for diffrent ports
        usb_address = {}
        usb_address['bus'] = '0x' + (usb_dev.split())[1]
        usb_address['device'] = '0x' + ((usb_dev.split())[3]).strip(':')
        usb_address['vendor_id'] = '0x' + \
            (((usb_dev.split())[5]).split(":"))[0]
        usb_address['product_id'] = '0x' + \
            (((usb_dev.split())[5]).split(":"))[1]
        vmxml.add_hostdev(usb_address, 'subsystem', 'usb', 'yes')
        # Start the guest after passthrough compare pci/modules/device
        # details with before passthrough
        try:
            vmxml.sync()
            # Starting VM since we ran sync in previous step. Else we get
            # VMDeadError
            vm.start()
            lsusb_op_aft = return_usbs_in_guest()
            logging.info(
                "USB devices within guest after passthrough: %s", lsusb_op_aft)
            if lsusb_op_bef == lsusb_op_aft:
                failures_reported = 1
                logging.info("Passthrough failed for USB device %s", usb_dev)
                fail_reported.append(usb_dev)
            else:
                logging.info("Passthrough passed for USB device %s", usb_dev)
                pass_reported.append(usb_dev)
        finally:
            backup_xml.sync()
    logging.info("Summary of USB device passthrough test: ")
    logging.info("Passthrough failed for USB devices %s", fail_reported)
    logging.info("Passthrough passed for USB devices %s", pass_reported)
    if failures_reported:
        test.fail("USB device passthrough failed for one or more"
                  "devices, see above output for more details")
