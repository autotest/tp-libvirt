import aexpect
import logging
import re

from avocado.core import exceptions
from avocado.utils import process

from virttest import utils_misc
from virttest import utils_net
from virttest import utils_test
from virttest import virsh
from virttest.libvirt_xml import vm_xml

from provider.sriov import sriov_base

LOG = logging.getLogger('avocado.' + __name__)


def comp_hostdev_xml(vm, device_type, hostdev_dict):
    """
    Compare the first hostdev xml of the VM

    :param vm: VM object
    :param device_type: device type
    :param hostdev_dict: hostdev parameters dict
    :raise: TestFail if comparison fails
    """
    vm_hostdev = vm_xml.VMXML.new_from_dumpxml(vm.name)\
        .devices.by_device_tag(device_type)[0]
    LOG.debug("hostdev XML: %s", vm_hostdev)
    vm_hostdev_dict = vm_hostdev.fetch_attrs()
    for attr, value in hostdev_dict.items():
        if attr == 'source':
            vm_hostdev_val = vm_hostdev_dict.get(attr).get('untyped_address')
            if not vm_hostdev_val:
                continue
            value = hostdev_dict[attr]['untyped_address']
        elif attr in ['address', 'hostdev_address']:
            vm_hostdev_val = vm_hostdev_dict.get(attr).get('attrs')
            value = hostdev_dict[attr]['attrs']
        else:
            if attr == "managed" and value == "no" and device_type == "interface":
                value = None
            vm_hostdev_val = vm_hostdev_dict.get(attr)

        if vm_hostdev_val != value:
            raise exceptions.TestFail('hostdev xml(%s) comparison failed.'
                                      'Expected "%s",got "%s".'
                                      % (attr, value, vm_hostdev_val))


def check_mac_addr(vm_session, vm_name, device_type, iface_dict):
    """
    Check VM interface's mac_address

    :param vm_session: VM session
    :param vm_name: VM name
    :param device_type: Device type, eg, interface or hostdev
    :param iface_dict: Interface dict
    :raises: TestFail if check fails
    """
    mac = iface_dict.get("mac_address")
    if device_type != 'interface' or not mac:
        LOG.warning("No need to check mac address.")
        return
    iface = utils_misc.wait_for(
        lambda: utils_net.get_linux_iface_info(mac=mac,
                                               session=vm_session), 120)
    if not iface:
        raise exceptions.TestFail("Unable to get VM interface with given mac "
                                  "address - %s!" % mac)
    if mac not in virsh.domiflist(vm_name, debug=True).stdout_text:
        raise exceptions.TestFail("Unable to get mac %s in virsh output!"
                                  % mac)


def check_mac_addr_recovery(dev_name, device_type, iface_dict):
    """
    Check if mac address is recovered

    :param dev_name: Device name
    :param device_type: Device type, eg, interface or hostdev
    :param iface_dict: Interface dict
    :raises: TestFail if mac address is not restored
    """
    mac = iface_dict.get("mac_address")
    if device_type != 'interface' or not mac:
        LOG.warning("No need to check mac address.")
        return
    cmd = "ip l show %s " % dev_name
    res = process.run(cmd, shell=True, verbose=True)
    if mac in res.stdout_text:
        raise exceptions.TestFail("Still get mac %s in %s!"
                                  % (mac, res.stdout_text))


def check_vlan(dev_name, iface_dict, status_error=False):
    """
    Check vlan setting

    :param dev_name: Device name
    :param iface_dict: Interface dict
    :param status_error: Whether the vlan should be cleared, defaults to False
    :raises: TestFail if check fails
    """
    if 'vlan' not in iface_dict:
        LOG.warning("No need to check vlan.")
        return
    cmd = "ip l show %s " % dev_name
    res = process.run(cmd, shell=True, verbose=True)
    vlan_check = re.findall("vf 0.*vlan (\d+)", res.stdout_text)
    if status_error:
        if vlan_check:
            raise exceptions.TestFail("Still get vlan value from '%s'!" % cmd)
    else:
        if not vlan_check:
            raise exceptions.TestFail("Unable to get vlan value from '%s'!"
                                      % cmd)
        act_value = vlan_check[0]
        exp_value = iface_dict['vlan']['tags'][0]['id']
        if act_value != exp_value:
            raise exceptions.TestFail("Incorrect vlan setting! Expected: %s, "
                                      "Actual: %s." % (exp_value, act_value))


def check_vm_network_accessed(vm_session, ping_count=3, ping_timeout=5,
                              tcpdump_iface=None, tcpdump_status_error=False,
                              ping_dest=None):
    """
    Test VM's network accessibility

    :param vm_session: The session object to the guest
    :param ping_count: The count value of ping command
    :param ping_timeout: The timeout of ping command
    :param tcpdump_iface: The interface to check
    :param tcpdump_status_error: Whether the tcpdump's output should include
        the string "ICMP echo request"
    :param ping_dest: Ping destination address
    :raise: test.fail when ping fails.
    :return: Output of ping command
    """
    if not ping_dest:
        ping_dest = sriov_base.get_ping_dest(vm_session)
    if tcpdump_iface:
        cmd = "tcpdump  -i %s icmp" % tcpdump_iface
        tcpdump_session = aexpect.ShellSession('bash')
        tcpdump_session.sendline(cmd)
    s, o = utils_test.ping(
        ping_dest, count=ping_count, timeout=ping_timeout, session=vm_session)
    if s:
        raise exceptions.TestFail("Failed to ping %s! status: %s, "
                                  "output: %s." % (ping_dest, s, o))
    if tcpdump_iface:
        output = tcpdump_session.get_stripped_output()
        LOG.debug("tcpdump's output: %s.", output)
        pat_str = "ICMP echo request"
        if re.search(pat_str, output):
            if tcpdump_status_error:
                exceptions.TestFail("Get incorrect tcpdump output: {}, it "
                                    "should not include '{}'."
                                    .format(output, pat_str))
        else:
            if not tcpdump_status_error:
                exceptions.TestFail("Get incorrect tcpdump output: {}, it "
                                    "should include '{}'."
                                    .format(output, pat_str))
    return o


def check_vm_iface_num(session, exp_num, timeout=10, first=0.0):
    """
    Check the number of interfaces

    :param session: The session to the guest
    :param exp_num: The expected number
    :param timeout: Timeout in seconds
    :param first: Time to sleep before first attempt
    :raise: test.fail when ifaces' number is incorrect.
    """
    p_iface = utils_misc.wait_for(
            lambda: utils_net.get_remote_host_net_ifs(session)[0],
            timeout, first=first)
    LOG.debug("Ifaces in VM: %s", p_iface)
    if p_iface is None:
        p_iface = []
    if len(p_iface) != exp_num:
        exceptions.TestFail("%d interface(s) should be found on the vm."
                            % exp_num)


def check_vm_iommu_group(vm_session, test_devices):
    """
    Check the devices with iommu enabled are located in a separate iommu group

    :param vm_session: VM session
    :param test_devices: test devices to check
    """
    for dev in test_devices:
        s, o = vm_session.cmd_status_output(
            "lspci | awk 'BEGIN{IGNORECASE=1} /%s/ {print $1}'" % dev)
        if s:
            exceptions.TestFail("Failed to get pci address!")

        cmd = ("find /sys/kernel/iommu_groups/ -type l | xargs ls -l | awk -F "
               "'/' '/%s / {print(\"\",$2,$3,$4,$5,$6)}' OFS='/' " % o.strip())
        s, o = vm_session.cmd_status_output(cmd)
        if s:
            exceptions.TestFail("Failed to find iommu group!")
        s, o = vm_session.cmd_status_output("ls %s" % o)
        if s:
            exceptions.TestFail("Failed to get the device in the iommu group!")
