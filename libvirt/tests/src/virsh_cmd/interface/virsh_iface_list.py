"""
Module to test virsh iface-xx commands including: iface-list, iface-dumpxml, iface-name, iface-mac
:author: Yalan Zhang <yalzhang@redhat.com>
:copyright: 2021 Red Hat Inc.
"""

import logging
import re

from avocado.utils import process, astring
from virttest import virsh


def run(test, params, env):
    """
    Test virsh interface related commands.

    As the netcf is deprecated on rhel 9, most of the iface* command will not be supported.
    Even on rhel 8, most of the commands are not recommended. Only iface-mac, iface-name,
    iface-list, iface-dumpxml are still supported. And the backend change to be udev.
    (1) Using exist interface for testing(eg. lo or ethX)
        1.1 List interfaces with '--inactive' optioin
        1.2 List interfaces with '--all' optioin
        1.3 List interfaces with no option
    (2) Dumpxml for the interface
        2.1 Dumpxml for the interface(with --inactive option)
        2.2 Dumpxml for the interface
    (3) Get interface MAC address by interface name
    (4) Get interface name by interface MAC address
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    opt = params.get('opt', '')
    test_list = "yes" == params.get("test_list", "no")
    test_info = "yes" == params.get("test_info", "no")
    test_mac = "yes" == params.get("test_mac", "no")
    test_name = "yes" == params.get("test_name", "no")

    def check_host_version():
        get_hostos_version = astring.to_text(process.run("cat /etc/redhat-release", shell=True).stdout)
        if re.search(r'(\d+(\.\d+)?)', get_hostos_version) is not None:
            hostos_version = float(re.search(r'(\d+(\.\d+)?)', get_hostos_version).group(0))
            if hostos_version < float(9.0):
                test.cancel("Skip the virsh iface-* command test, only test it on RHEL 9 or above")
        else:
            test.cancel("Skip the test as failed to get the os version")

    def check_status(iface_name, status):
        """
        Test the status in the iface-list output for interface

        param iface_name: the interface to be checked
        status: the expected status of the interface, active or inactive
        return: True or False
        """
        o = process.run("cat /sys/class/net/%s/carrier" % iface_name, shell=True).stdout_text
        if o.strip() == '0' and status == 'active':
            return False
        elif o.strip() == '1' and status == 'inactive':
            return False
        return True

    def test_dumpxml_info(iface_name):
        """
        Check the information from iface-dumpxml for interface iface_name, including mac, mtu, link state

        Param iface_name: the interface name
        Return: True or False
        """
        # get the info from iface-dumpxml virsh cmd
        logging.debug("Check the dumpxml info for interface %s",  iface_name)
        output = virsh.iface_dumpxml(iface_name)
        logging.debug("the dumpxml of interface %s is %s", iface, output)
        mtu = re.search(r"size='(.*)'", output, re.M | re.I).group(1)
        link_state = re.search(r"state='(.*)'", output, re.M | re.I).group(1)
        mac = re.search(r"address='(.*)'", output, re.M | re.I).group(1)
        # check the info in the output, includes interface type, mtu, link state, mac
        # compared with system device info
        mac_file = process.run("cat /sys/class/net/%s/address" % iface_name, shell=True).stdout_text.strip()
        carrier = process.run("cat /sys/class/net/%s/carrier" % iface_name, shell=True).stdout_text.strip()
        mtu_file = process.run("cat /sys/class/net/%s/mtu" % iface_name, shell=True).stdout_text.strip()
        if mac != mac_file:
            logging.debug("Interface %s mac check fail", iface_name)
            return False
        if (link_state == "up" and carrier != "1") or (link_state == "down" and carrier != "0"):
            logging.debug("Interface %s carrier check fail", iface_name)
            return False
        if mtu != mtu_file:
            logging.debug("Interface %s mtu check fail", iface_name)
            return False
        return True

    check_host_version()
    # run test cases
    if vm.is_alive():
        vm.destroy()

    try:
        output_all = virsh.iface_list("--all").stdout_text
        logging.debug("the outputs of the iface_list is %s", output_all)
        # Get the list for all the interface, active interface, inactive interface based on the iface-list --all output
        interface = []
        inactive_interface = []
        active_interface = []
        for line in output_all.splitlines():
            logging.debug("the line is %s", line)
            iface_ = [x for x in line.split() if x]
            if "active" in iface_:
                active_interface.append(iface_[0])
                interface.append(iface_[0])
            elif "inactive" in iface_:
                inactive_interface.append(iface_[0])
                interface.append(iface_[0])
        logging.debug("all interface get from the virsh iface-list is %s", interface)
        logging.debug("inactive interface get from the virsh iface-list is %s", inactive_interface)
        logging.debug("active interface is %s", active_interface)
        # check all the interface has been listed compared with system udev info
        if test_list:
            all_iface = process.run("ls /sys/class/net", shell=True).stdout_text.split('\n')[:-1]
            logging.debug("all interfaces get from the /sys/class/net is %s", all_iface)
            # do not include the tap device
            tuntap = process.run("ip tuntap", shell=True).stdout_text
            for x in all_iface:
                if x in tuntap:
                    all_iface.remove(x)
            logging.debug("after delete the tuntap device: %s", all_iface)
            interface_ = set(interface)
            all_iface_ = set(all_iface)
            if interface_ != all_iface_:
                test.fail("Interface existence check for 'iface-list --all' fail compared with system device info!")
            # check the active and inactive attribute from the iface-list is correct
            for item in active_interface:
                if not check_status(item, 'active'):
                    test.fail("The interface %s should be inactive in virsh iface-list, but it is not." % item)
            for item in inactive_interface:
                if not check_status(item, 'inactive'):
                    test.fail("The interface %s should be active in virsh iface-list, but it is not." % item)
            # check the filter of the flag is effective, as above check compares all interfaces with /sys/class/net,
            # now comparing with the the "iface-list --all" outputs is enough
            output = virsh.iface_list(opt).stdout_text
            cmd_iface = []
            for line in output.splitlines():
                iface_ = [x for x in line.split() if x]
                if "active" in iface_ or "inactive" in iface_:
                    cmd_iface.append(iface_[0])
            if opt == '--inactive' and set(cmd_iface) != set(inactive_interface):
                test.fail("iface-list --inactive should list all inactive interface, but it is not")
            elif opt == '' and set(cmd_iface) != set(active_interface):
                test.fail("iface-list should list all active interface by default, but it is not")
        elif test_info:
            for iface in interface:
                if not iface.startswith("virbr"):
                    s = test_dumpxml_info(iface)
                    if not s:
                        test.fail("Checking info in dumpxml fail for %s" % iface)
                else:
                    out = process.run('ip addr show %s' % iface, shell=True).stdout_text.strip()
                    mac = re.search(r"ether (.*) brd", out, re.M | re.I).group(1)
                if test_mac:
                    # test iface-mac command
                    mac_cmd = virsh.iface_mac(iface).stdout_text.strip()
                    if mac_cmd not in process.run("ip l show %s" % iface).stdout_text:
                        test.fail("the mac address get from virsh iface-mac for %s does not match" % iface)
                if test_name:
                    # test iface-name command
                    name_cmd = virsh.iface_name(mac).stdout_text.strip()
                    if iface != name_cmd:
                        test.fail("The name get from virsh iface-name for %s is not expexted" % mac)
    finally:
        logging.info("Test case finished, and no need to restore any environment")
