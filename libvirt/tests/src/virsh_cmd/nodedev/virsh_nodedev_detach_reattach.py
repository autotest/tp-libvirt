import os
import logging
from virttest import virsh
from virttest.libvirt_xml import nodedev_xml
from virttest import libvirt_version
from virttest.utils_test import libvirt
from avocado.utils import process
from avocado.core import exceptions


def run(test, params, env):
    """
    Test virsh nodedev-detach and virsh nodedev-reattach

    Step1.Init variables for test.
    Step2.Check variables.
    Step3.Do nodedev_detach_reattach.
    """
    def get_driver_readlink(device_address):
        """
        Readlink the driver of device.
        """
        nodedevxml = nodedev_xml.NodedevXML.new_from_dumpxml(device_address)
        driver_path = ('%s/driver') % (nodedevxml.get_sysfs_path())
        try:
            driver = os.readlink(driver_path)
        except (OSError, UnicodeError):
            return None
        return driver

    def get_device_driver(device_address):
        """
        Get the driver of device.
        :param device_address: The address of device, such as pci_0000_19_00_0
        :return: The driver of device, such as ixgbe, igb
        """
        driver_strings = get_driver_readlink(device_address).strip().split('/')
        driver = driver_strings[-1]
        return driver

    def detach_reattach_nodedev(device_address, params, options=""):
        """
        Do the detach and reattach.

        Step1.Do detach.
        Step2.Check the result of detach.
        Step3.Do reattach.
        Step4.Check the result of reattach
        """
        # Libvirt acl polkit related params
        uri = params.get("virsh_uri")
        unprivileged_user = params.get('unprivileged_user')
        readonly = (params.get('nodedev_detach_readonly', 'no') == 'yes')
        if unprivileged_user:
            if unprivileged_user.count('EXAMPLE'):
                unprivileged_user = 'testacl'

        # Do the detach
        logging.debug('Node device name is %s.', device_address)
        CmdResult = virsh.nodedev_detach(device_address, options,
                                         unprivileged_user=unprivileged_user,
                                         uri=uri, readonly=readonly)
        # Check the exit_status.
        libvirt.check_exit_status(CmdResult)
        # Check the driver.
        driver = get_driver_readlink(device_address)
        logging.debug('Driver after detach is %s.', driver)
        if libvirt_version.version_compare(1, 1, 1):
            device_driver_name = 'vfio-pci'
        else:
            device_driver_name = 'pci-stub'
        if (driver is None) or (not driver.endswith(device_driver_name)):
            test.fail("Driver for %s is not %s "
                      "after nodedev-detach" % (device_address, device_driver_name))
        # Do the reattach.
        CmdResult = virsh.nodedev_reattach(device_address, options)
        # Check the exit_status.
        libvirt.check_exit_status(CmdResult)
        # Check the driver.
        driver = get_driver_readlink(device_address)
        if libvirt_version.version_compare(1, 1, 1):
            device_driver_name = 'vfio-pci'
        else:
            device_driver_name = 'pci-stub'
        if driver and driver.endswith(device_driver_name):
            test.fail("Driver for %s is not %s "
                      "after nodedev-detach" % (device_address, device_driver_name))

    def pci_device_address():
        """
        Get the address of pci device
        """
        net_list = virsh.nodedev_list(tree='', cap='net')
        net_lists = net_list.stdout.strip().splitlines()
        route_cmd = " route | grep default"
        route_default = process.run(route_cmd, shell=True).stdout_text.strip().split(' ')
        ip_default = route_default[-1]

        for default_net_name in net_lists:
            if default_net_name.find(ip_default):
                default_net_address = nodedev_xml.NodedevXML.new_from_dumpxml(default_net_name).parent
                default_net_driver = get_device_driver(default_net_address)
                break
        for net_device_name in net_lists:
            if net_device_name.find(ip_default) == -1:
                net_device_address = nodedev_xml.NodedevXML.new_from_dumpxml(net_device_name).parent
                if 'pci' in net_device_address:
                    net_device_driver = get_device_driver(net_device_address)
                    if net_device_driver != default_net_driver:
                        return net_device_address

    def check_kernel_option():
        """
        Check the kernel option if the kernel cmdline include  "iommu=on" option
        """
        check_cmd = "egrep '(intel|amd)_iommu=on' /proc/cmdline"
        try:
            check_result = process.run(check_cmd, shell=True)
        except Exception:
            test.cancel("Operation not supported: neither VFIO nor KVM device assignment"
                        "is currently supported on this system")
        else:
            logging.debug('IOMMU is enabled')

    #Check kernel iommu option
    check_kernel_option()

    # Init variables
    device_address = params.get('nodedev_device', 'ENTER.YOUR.PCI.DEVICE.TO.DETACH')
    if device_address.find('ENTER.YOUR.PCI.DEVICE.TO.DETACH') != -1:
        replace_address = pci_device_address()
        if replace_address:
            device_address = device_address.replace('ENTER.YOUR.PCI.DEVICE.TO.DETACH', replace_address)
        else:
            test.cancel('Param device_address is not configured.')
    device_opt = params.get('nodedev_device_opt', '')
    status_error = ('yes' == params.get('status_error', 'no'))
    with_driver = params.get('with_driver', 'yes') == 'yes'
    # check variables.
    if not libvirt_version.version_compare(1, 1, 1):
        if params.get('setup_libvirt_polkit') == 'yes':
            test.cancel("API acl test not supported in current"
                        " libvirt version.")

    # check the device driver and delete the driver
    if not with_driver:
        device_driver = get_device_driver(device_address)
        remove_cmd = "modprobe -r %s" % device_driver
        remove_opt = process.system(remove_cmd, shell=True)
        if remove_opt != 0:
            test.fail("Fail to remove the device driver : %s" % device_driver)
    # Do nodedev_detach_reattach
    try:
        detach_reattach_nodedev(device_address, params, device_opt)
    except exceptions.TestFail as e:
        # Do nodedev detach and reattach failed.
        if status_error:
            return
        else:
            test.fail("Test failed in positive case."
                      "error: %s" % e)

    # Do nodedev detach and reattach success.
    if status_error:
        test.fail('Test successed in negative case.')

    # reload the device driver
    if not with_driver:
        reload_cmd = "modprobe %s" % device_driver
        reload_opt = process.system(reload_cmd, shell=True)
        if reload_opt != 0:
            test.fail("Fail to reload the device driver : %s" % device_driver)
