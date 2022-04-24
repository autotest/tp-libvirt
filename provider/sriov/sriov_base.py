import re

from avocado.core import exceptions
from avocado.utils import process

from virttest import utils_misc
from virttest import utils_net
from virttest import utils_package
from virttest import utils_sriov
from virttest import utils_test
from virttest import virsh
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_libvirt import libvirt_network

from provider.interface import interface_base


def setup_vf(pf_pci, params):
    """
    Enable vf setting

    :param pf_pci: The pci of PF
    :return: The original vf value
    """
    default_vf = 0
    try:
        vf_no = int(params.get("vf_no", "4"))
    except ValueError as e:
        raise exceptions.TestError(e)
    pf_pci_path = utils_misc.get_pci_path(pf_pci)
    cmd = "cat %s/sriov_numvfs" % (pf_pci_path)
    default_vf = process.run(cmd, shell=True, verbose=True).stdout_text
    if not utils_sriov.set_vf(pf_pci_path, vf_no):
        raise exceptions.TestError("Failed to set vf.")
    if not utils_misc.wait_for(lambda: process.run(
       cmd, shell=True, verbose=True).stdout_text.strip() == str(vf_no),
       30, 5):
        raise exceptions.TestError("VF's number should set to %d." % vf_no)
    return default_vf


def recover_vf(pf_pci, params, default_vf=0, timeout=60):
    """
    Recover vf setting

    :param pf_pci: The pci of PF
    :param params: the parameters dict
    :param default_vf: The value to be set
    :param timeout: Timeout in seconds
    """
    pf_pci_path = utils_misc.get_pci_path(pf_pci)
    vf_no = int(params.get("vf_no", "4"))
    if default_vf != vf_no:
        utils_sriov.set_vf(pf_pci_path, default_vf, timeout=timeout)


def get_ping_dest(vm_session, mac_addr="", restart_network=False):
    """
    Get an ip address to ping

    :param vm_session: The session object to the guest
    :param mac_addr: mac address of given interface
    :param restart_network:  Whether to restart guest's network
    :return: ip address
    """
    if restart_network:
        if not utils_package.package_install('dhcp-client', session=vm_session):
            raise exceptions.TestFail("Failed to install dhcp-client on guest.")
        utils_net.restart_guest_network(vm_session)
    vm_iface = utils_net.get_linux_ifname(vm_session, mac_addr)
    if isinstance(vm_iface, list):
        iface_name = vm_iface[0]
    else:
        iface_name = vm_iface
    utils_misc.wait_for(
         lambda: utils_net.get_net_if_addrs(
            iface_name, vm_session.cmd_output).get('ipv4'), 20)
    cmd = ("ip route |awk -F '/' '/^[0-9]/, /dev %s/ {print $1}'" % iface_name)
    status, output = utils_misc.cmd_status_output(cmd, shell=True,
                                                  session=vm_session)
    if status or not output:
        raise exceptions.TestError("Failed to run cmd - {}, status - {}, "
                                   "output - {}.".format(cmd, status, output))
    return re.sub('\d+$', '1', output.strip().splitlines()[-1])


def check_vm_network_accessed(vm_session, ping_count=3, ping_timeout=5):
    """
    Test VM's network accessibility

    :param vm_session: The session object to the guest
    :param ping_count: The count value of ping command
    :param ping_timeout: The timeout of ping command
    :raise: test.fail when ping fails.
    """
    ping_dest = get_ping_dest(vm_session)
    s, o = utils_test.ping(
        ping_dest, count=ping_count, timeout=ping_timeout, session=vm_session)
    if s:
        raise exceptions.TestFail("Failed to ping %s! status: %s, "
                                  "output: %s." % (ping_dest, s, o))


class SRIOVTest(object):
    """
    Wrapper class for sriov testing
    """
    def __init__(self, vm, test, params):
        self.vm = vm
        self.test = test
        self.params = params
        self.pf_pci = utils_sriov.get_pf_pci()
        if not self.pf_pci:
            test.cancel("NO available pf found.")
        self.pf_pci_path = utils_misc.get_pci_path(self.pf_pci)

        utils_sriov.set_vf(self.pf_pci_path, 0)
        setup_vf(self.pf_pci, self.params)
        self.pf_info = utils_sriov.get_pf_info_by_pci(self.pf_pci)
        self.vf_pci = utils_sriov.get_vf_pci_id(self.pf_pci)
        self.pf_pci_addr = utils_sriov.pci_to_addr(self.pf_pci)
        self.vf_pci_addr = utils_sriov.pci_to_addr(self.vf_pci)
        self.pf_name = self.pf_info.get('iface')
        self.vf_name = utils_sriov.get_iface_name(self.vf_pci)
        self.pf_dev_name = utils_sriov.get_device_name(self.pf_pci)
        self.vf_dev_name = utils_sriov.get_device_name(self.vf_pci)

    def __del__(self):
        recover_vf(self.pf_pci, self.params, 0)

    def parse_iface_dict(self):
        """
        Parse iface_dict from params

        :return: The updated iface_dict
        """
        mac_addr = utils_net.generate_mac_address_simple()
        pf_pci_addr = self.pf_pci_addr
        vf_pci_addr = self.vf_pci_addr
        pf_name = self.pf_name
        vf_name = self.vf_name
        if self.params.get('iface_dict'):
            iface_dict = eval(self.params.get('iface_dict', '{}'))
        else:
            del pf_pci_addr['type']
            del vf_pci_addr['type']
            iface_dict = eval(self.params.get('hostdev_dict', '{}'))

        self.test.log.debug("iface_dict: %s.", iface_dict)
        return iface_dict

    def parse_network_dict(self):
        """
        Parse network dict from params

        :return: The updated network dict
        """
        vf_pci_addr = self.vf_pci_addr
        pf_name = self.pf_name
        net_forward_pf = str({'dev': pf_name})
        vf_list_attrs = str([vf_pci_addr])
        net_dict = eval(self.params.get('network_dict', '{}'))
        self.test.log.debug("network_dict: %s.", net_dict)
        return net_dict

    def get_dev_name(self):
        """
        Get device name

        :return: Device name, eg. pci_0000_05_00_1
        """
        dev_source = self.params.get("dev_source", "")
        dev_name = ""
        if dev_source.startswith("vf_"):
            dev_name = utils_sriov.get_device_name(self.vf_pci)
        elif dev_source.startswith("pf_"):
            dev_name = utils_sriov.get_device_name(self.pf_pci)
        elif dev_source == "network":
            dev_name = utils_sriov.get_device_name(self.pf_pci)
        else:
            self.test.log.warning("Unkown device source.")
        return dev_name

    def create_iface_dev(self, dev_type, iface_dict):
        """
        Create an interface device

        :param dev_type: Device type, hostdev device or interface
        :param iface_dict: Interface dict to create
        :return: Hostdev device or interface device object
        """
        if dev_type == "hostdev_device":
            iface_dev = interface_base.create_hostdev(iface_dict)
        else:
            iface_dev = interface_base.create_iface(dev_type, iface_dict)
        return iface_dev

    def setup_default(self, dev_name, managed_disabled, network_dict=None):
        """
        Default setup

        :param dev_name: Device name, eg. pci_0000_05_00_1
        :param managed_disabled: Whether to enable managed
        :param network_dict: Network dict
        """
        self.test.log.info("TEST_SETUP: Clear up the existing VM "
                           "interface(s) before testing.")
        libvirt_vmxml.remove_vm_devices_by_type(self.vm, 'interface')
        if network_dict:
            self.test.log.info("TEST_SETUP: Create new network.")
            libvirt_network.create_or_del_network(network_dict)

        if managed_disabled:
            virsh.nodedev_detach(dev_name, debug=True, ignore_errors=False)

    def teardown_default(self, orig_config_xml, managed_disabled, dev_name,
                         network_dict=None):
        """
        Default cleanup

        :param orig_config_xml: Original VM xml to recover
        :param managed_disabled: Whether to enable managed
        :param dev_name: Device name
        :param network_dict: Network dict
        """
        self.test.log.info("TEST_TEARDOWN: Recover test enviroment.")
        orig_config_xml.sync()
        if managed_disabled:
            virsh.nodedev_reattach(dev_name, debug=True, ignore_errors=False)
        if network_dict:
            libvirt_network.create_or_del_network(network_dict, True)
