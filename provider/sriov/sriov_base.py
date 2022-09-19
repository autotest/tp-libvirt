import re

from avocado.core import exceptions

from virttest import libvirt_version
from virttest import utils_libvirtd
from virttest import utils_misc
from virttest import utils_net
from virttest import utils_package
from virttest import utils_sriov
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_libvirt import libvirt_virtio
from virttest.utils_libvirt import libvirt_network

from provider.interface import interface_base


def setup_vf(pf_pci, params, session=None):
    """
    Enable vf setting

    :param pf_pci: The pci of PF
    :param session: The session object to the host
    :return: The original vf value
    """
    default_vf = 0
    try:
        vf_no = int(params.get("vf_no", "4"))
    except ValueError as e:
        raise exceptions.TestError(e)
    pf_pci_path = utils_misc.get_pci_path(pf_pci, session=session)
    cmd = "cat %s/sriov_numvfs" % (pf_pci_path)
    default_vf = utils_misc.cmd_status_output(cmd, shell=True, verbose=True)[1]
    if not utils_sriov.set_vf(pf_pci_path, vf_no, session=session):
        raise exceptions.TestError("Failed to set vf.")
    if not utils_misc.wait_for(lambda: utils_misc.cmd_status_output(
       cmd, shell=True, verbose=True, session=session)[1].strip() == str(vf_no),
       30, 5):
        raise exceptions.TestError("VF's number should set to %d." % vf_no)
    return default_vf


def recover_vf(pf_pci, params, default_vf=0, timeout=60, session=None):
    """
    Recover vf setting

    :param pf_pci: The pci of PF
    :param params: the parameters dict
    :param default_vf: The value to be set
    :param timeout: Timeout in seconds
    :param session: The session object to the host
    """
    pf_pci_path = utils_misc.get_pci_path(pf_pci, session=session)
    vf_no = int(params.get("vf_no", "4"))
    if default_vf != vf_no:
        utils_sriov.set_vf(pf_pci_path, default_vf, session=session,
                           timeout=timeout)


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
        raise exceptions.TestError("Unable to get VM ip address! status - {}, "
                                   "output - {}.".format(status, output))
    return re.sub('\d+$', '1', output.strip().splitlines()[-1])


class SRIOVTest(object):
    """
    Wrapper class for sriov testing
    """
    def __init__(self, vm, test, params, session=None):
        self.vm = vm
        self.test = test
        self.params = params
        self.session = session
        self.remote_virsh_dargs = None
        if self.session:
            self.server_ip = self.params.get("server_ip")
            self.server_user = self.params.get("server_user", "root")
            self.server_pwd = self.params.get("server_pwd")
            self.remote_virsh_dargs = {'remote_ip': self.server_ip,
                                       'remote_user': self.server_user,
                                       'remote_pwd': self.server_pwd,
                                       'unprivileged_user': None,
                                       'ssh_remote_auth': True}
        libvirt_version.is_libvirt_feature_supported(self.params)
        self.pf_pci = utils_sriov.get_pf_pci(session=self.session)
        if not self.pf_pci:
            test.cancel("NO available pf found.")
        self.pf_pci_path = utils_misc.get_pci_path(self.pf_pci,
                                                   session=self.session)

        utils_sriov.set_vf(self.pf_pci_path, 0, session=self.session)
        setup_vf(self.pf_pci, self.params, session=self.session)
        self.pf_info = utils_sriov.get_pf_info_by_pci(
            self.pf_pci, session=self.session)
        self.vf_pci = utils_sriov.get_vf_pci_id(
            self.pf_pci, session=self.session)
        self.vf_pci2 = utils_sriov.get_vf_pci_id(
            self.pf_pci, 1, session=self.session)
        self.pf_pci_addr = utils_sriov.pci_to_addr(self.pf_pci)
        self.vf_pci_addr = utils_sriov.pci_to_addr(self.vf_pci)
        self.vf_pci_addr2 = utils_sriov.pci_to_addr(self.vf_pci2)
        self.pf_name = self.pf_info.get('iface')
        self.vf_name = utils_sriov.get_iface_name(
            self.vf_pci, session=self.session)
        self.pf_dev_name = utils_sriov.get_device_name(self.pf_pci)
        self.vf_dev_name = utils_sriov.get_device_name(self.vf_pci)
        self.default_vf_mac = utils_sriov.get_vf_mac(
            self.pf_name, session=self.session)
        self.vf_mac = ""

        new_xml = vm_xml.VMXML.new_from_inactive_dumpxml(self.vm.name)
        self.orig_config_xml = new_xml.copy()

    def __del__(self):
        recover_vf(self.pf_pci, self.params, 0, session=self.session)

    def parse_iface_dict(self):
        """
        Parse iface_dict from params

        :return: The updated iface_dict
        """
        mac_addr = utils_net.generate_mac_address_simple()
        pf_pci_addr = self.pf_pci_addr
        vf_pci_addr = self.vf_pci_addr
        vf_pci_addr2 = self.vf_pci_addr2
        pf_name = self.pf_name
        vf_name = self.vf_name
        if self.params.get('iface_dict'):
            iface_dict = eval(self.params.get('iface_dict', '{}'))
        else:
            if pf_pci_addr.get('type'):
                del pf_pci_addr['type']
            if vf_pci_addr.get('type'):
                del vf_pci_addr['type']
            if vf_pci_addr2.get('type'):
                del vf_pci_addr2['type']
            iface_dict = eval(self.params.get('hostdev_dict', '{}'))

        self.test.log.debug("iface_dict: %s.", iface_dict)
        return iface_dict

    def parse_network_dict(self):
        """
        Parse network dict from params

        :return: The updated network dict
        """
        vf_pci_addr = self.vf_pci_addr
        vf_pci_addr2 = self.vf_pci_addr2
        pf_pci_addr = self.pf_pci_addr
        pf_name = self.pf_name
        vf_name = self.vf_name
        net_forward_pf = str({'dev': pf_name})
        vf_list_attrs = str([vf_pci_addr])
        net_dict = eval(self.params.get('network_dict', '{}'))
        self.test.log.debug("network_dict: %s.", net_dict)
        return net_dict

    def parse_iommu_test_params(self):
        """
        Parse iommu test dict from params

        :return: The updated test dict
        """
        test_scenario = self.params.get("test_scenario", "")
        dev_type = self.params.get("dev_type", "hostdev_interface")
        mac_addr = self.parse_iface_dict().get('mac_address', '')
        iommu_dict = eval(self.params.get('iommu_dict', '{}'))
        br_dict = eval(self.params.get('br_dict',
                                       "{'source': {'bridge': 'br0'}}"))

        iommu_params = {"iommu_dict": iommu_dict,
                        "test_scenario": test_scenario,
                        "br_dict": br_dict, "dev_type": dev_type}
        return iommu_params

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

    def setup_default(self, **dargs):
        """
        Default setup

        :param dargs: test keywords
        """
        dev_name = dargs.get('dev_name')
        managed_disabled = dargs.get('managed_disabled', False)
        network_dict = dargs.get("network_dict", {})
        self.test.log.info("TEST_SETUP: Clear up the existing VM "
                           "interface(s) before testing.")
        libvirt_vmxml.remove_vm_devices_by_type(self.vm, 'interface')
        libvirt_vmxml.remove_vm_devices_by_type(self.vm, 'hostdev')
        if network_dict:
            self.test.log.info("TEST_SETUP: Create new network.")
            libvirt_network.create_or_del_network(network_dict)

        if managed_disabled:
            virsh.nodedev_detach(dev_name, debug=True, ignore_status=False)

    def teardown_default(self, **dargs):
        """
        Default cleanup

        :param dargs: test keywords
        """
        dev_name = dargs.get('dev_name')
        managed_disabled = dargs.get('managed_disabled', False)
        network_dict = dargs.get("network_dict", {})
        self.test.log.info("TEST_TEARDOWN: Recover test environment.")
        if self.vm.is_alive():
            self.vm.destroy(gracefully=False)
        try:
            self.orig_config_xml.sync()
        except:
            # FIXME: Workaround for 'save'/'managedsave' hanging issue
            utils_libvirtd.Libvirtd().restart()
            self.orig_config_xml.sync()
        if managed_disabled:
            virsh.nodedev_reattach(dev_name, debug=True, ignore_status=False)
        if network_dict:
            libvirt_network.create_or_del_network(network_dict, True)

    def setup_failover_test(self, **dargs):
        """
        Setup for failover test

        :param dargs: test keywords
        """
        br_network_dict = eval(self.params.get("br_network_dict", '{}'))
        br_name = self.params.get("br_name", "br0")
        network_dict = self.parse_network_dict()
        iface_dict = self.parse_iface_dict()
        mac_addr = iface_dict.get('mac_address', '')
        br_dict = eval(self.params.get("br_dict", '{}'))

        if self.params.get("set_vf_mac", False):
            if not br_dict.get('mac_address'):
                br_dict['mac_address'] = utils_net.generate_mac_address_simple()
            self.vf_mac = br_dict['mac_address']
            self.test.log.debug("Set vf's mac to %s.", self.vf_mac)
            utils_sriov.set_vf_mac(self.pf_name, self.vf_mac,
                                   session=self.session)

        self.test.log.info("TEST_SETUP: Create host bridge.")
        utils_sriov.add_connection(self.pf_name, br_name, self.session)

        if network_dict:
            self.test.log.info("TEST_SETUP: Create network for %s",
                               network_dict)
            libvirt_network.create_or_del_network(
                network_dict, remote_args=self.remote_virsh_dargs)
        if br_network_dict:
            self.test.log.info("TEST_SETUP: Create network for %s",
                               br_network_dict)
            libvirt_network.create_or_del_network(
                br_network_dict, remote_args=self.remote_virsh_dargs)
        if not self.session:
            libvirt_vmxml.remove_vm_devices_by_type(self.vm, 'interface')
            libvirt_vmxml.remove_vm_devices_by_type(self.vm, 'hostdev')

            self.test.log.info("TEST_SETUP: Add bridge interface.")
            br_dev = self.create_iface_dev("interface", br_dict)
            libvirt.add_vm_device(
                vm_xml.VMXML.new_from_dumpxml(self.vm.name), br_dev)

    def teardown_failover_test(self, **dargs):
        """
        Teardown for failover test

        :param dargs: test keywords
        """
        br_name = self.params.get("br_name", "br0")

        if self.params.get("set_vf_mac", False):
            self.test.log.debug("Recover vf's mac to %s.", self.default_vf_mac)
            utils_sriov.set_vf_mac(self.pf_name, self.default_vf_mac, session=self.session)
        if not self.session:
            self.teardown_default(**dargs)
        self.test.log.info("TEST_TEARDOWN: Remove host bridge.")
        utils_sriov.del_connection(self.pf_name, br_name, self.session)

        network_dict = self.parse_network_dict()
        br_network_dict = eval(self.params.get("br_network_dict", '{}'))

        if network_dict:
            libvirt_network.create_or_del_network(
                network_dict, True, remote_args=self.remote_virsh_dargs)
        if br_network_dict:
            libvirt_network.create_or_del_network(
                br_network_dict, True, remote_args=self.remote_virsh_dargs)

    def setup_iommu_test(self, **dargs):
        """
        iommu test environment setup

        :param dargs: Other test keywords
        """
        iommu_dict = dargs.get('iommu_dict', {})
        test_scenario = dargs.get('test_scenario', '')
        br_dict = dargs.get('br_dict', "{'source': {'bridge': 'br0'}}")
        brg_dict = {'pf_name': self.pf_name,
                    'bridge_name': br_dict['source']['bridge']}
        dev_type = dargs.get("dev_type", "interface")

        self.setup_default(**dargs)
        if iommu_dict:
            self.test.log.info("TEST_SETUP: Add iommu device.")
            libvirt_virtio.add_iommu_dev(self.vm, iommu_dict)

        if test_scenario == "failover":
            self.test.log.info("TEST_SETUP: Create host bridge.")
            utils_sriov.add_or_del_connection(brg_dict)
            self.test.log.info("TEST_SETUP: Add bridge interface.")
            br_dev = self.create_iface_dev(dev_type, br_dict)
            libvirt.add_vm_device(
                vm_xml.VMXML.new_from_dumpxml(self.vm.name), br_dev)

    def teardown_iommu_test(self, **dargs):
        """
        Cleanup iommu test environment

        :param dargs: Other test keywords
        """
        test_scenario = dargs.get('test_scenario', '')
        br_dict = dargs.get('br_dict', "{'source': {'bridge': 'br0'}}")
        brg_dict = {'pf_name': self.pf_name,
                    'bridge_name': br_dict['source']['bridge']}
        self.teardown_default(**dargs)
        if test_scenario == 'failover':
            self.test.log.info("TEST_TEARDOWN: Remove host bridge.")
            utils_sriov.add_or_del_connection(brg_dict, is_del=True)
