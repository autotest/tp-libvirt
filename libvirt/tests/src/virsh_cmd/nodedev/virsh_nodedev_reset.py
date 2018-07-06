import os
import re
import logging
from virttest import virsh
from virttest import utils_libvirtd
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Test command: virsh nodedev-reset <device>
    When `device_option` is:
    1) resettable   : Reset specified device if it is resettable.
    2) non-exist    : Try to reset specified device which doesn't exist.
    3) non-pci      : Try to reset all local non-PCI devices.
    4) unresettable : Try to reset all unresettable PCI devices.
    """

    def get_pci_info():
        """
        Get infomation for all PCI devices including:
        1) whether device has reset under its sysfs dir.
        2) Whether device has driver dir under its sysfs dir.

        :return: A dict using libvirt canonical nodedev name as keys
                 and dicts like {'reset': True, 'driver': True} as values
        """
        devices = {}
        pci_path = '/sys/bus/pci/devices'
        for device in os.listdir(pci_path):
            # Generate a virsh nodedev format device name
            dev_name = re.sub(r'\W', '_', 'pci_' + device)

            dev_path = os.path.join(pci_path, device)
            # Check whether device has `reset` file
            reset_path = os.path.join(dev_path, 'reset')
            has_reset = os.path.isfile(reset_path)

            # Check whether device has `driver` file
            driver_path = os.path.join(dev_path, 'driver')
            has_driver = os.path.isdir(driver_path)

            info = {'reset': has_reset, 'driver': has_driver}
            devices[dev_name] = info
        return devices

    def test_nodedev_reset(devices, expect_error, **virsh_dargs):
        """
        Test nodedev-reset command on a list of devices

        :param devices        : A list of node devices to be tested.
        :param expect_error : 'yes' for expect command run successfully
                                 and 'no' for fail.
        :param virsh_dargs: standardized virsh function API keywords
        """
        readonly = virsh_dargs.get('readonly', 'no')
        for device in devices:
            result = virsh.nodedev_reset(device, readonly=readonly, debug=True)
            # Check whether exit code match expectation.
            libvirt.check_exit_status(result, expect_error)

    # Retrive parameters
    expect_error = params.get('expect_error', 'no') == 'yes'
    device_option = params.get('device_option', 'valid')
    unspecified = 'REPLACE_WITH_TEST_DEVICE'
    readonly = (params.get('nodedev_reset_readonly', 'no') == 'yes')

    # Backup original libvirtd status and prepare libvirtd status
    logging.debug('Preparing libvirtd')
    libvirtd = utils_libvirtd.Libvirtd()
    if params.get("libvirtd", "on") == "off":
        libvirtd.stop()

    # Get whether PCI devices are resettable from sysfs.
    devices = get_pci_info()

    # Devide PCI devices into to catagories.
    resettable_nodes = []
    unresettable_nodes = []
    for device in devices:
        info = devices[device]
        if info['reset'] and info['driver']:
            resettable_nodes.append(device)
        if not info['reset'] and not info['driver']:
            unresettable_nodes.append(device)

    # Find out all non-PCI devices.
    all_devices = virsh.nodedev_list().stdout.strip().splitlines()
    non_pci_nodes = []
    for device in all_devices:
        if device not in devices:
            non_pci_nodes.append(device)

    try:
        if device_option == 'resettable':
            specified_device = resettable_nodes[0]
            # Test specified resettable device.
            if specified_device != unspecified:
                if specified_device in resettable_nodes:
                    test_nodedev_reset([specified_device], expect_error, readonly=readonly)
                else:
                    test.error('Param specified_device is not set!')
            else:
                test.cancel('Param specified_device is not set!')
        elif device_option == 'non-exist':
            specified_device = params.get('specified_device', unspecified)
            # Test specified non-exist device.
            if specified_device != unspecified:
                if specified_device not in all_devices:
                    test_nodedev_reset([specified_device], expect_error)
                else:
                    test.error('Specified device exists!')
            else:
                test.cancel('Param specified_device is not set!')
        elif device_option == 'non-pci':
            # Test all non-PCI device.
            if non_pci_nodes:
                test_nodedev_reset(non_pci_nodes, expect_error)
            else:
                test.cancel('No non-PCI device found!')
        elif device_option == 'unresettable':
            # Test all unresettable device.
            if unresettable_nodes:
                test_nodedev_reset(unresettable_nodes, expect_error)
            else:
                test.cancel('No unresettable device found!')
        else:
            test.error('Unrecognisable device option %s!' % device_option)
    finally:
        # Restore libvirtd status
        logging.debug('Restoring libvirtd')
        if not libvirtd.is_running():
            libvirtd.start()
