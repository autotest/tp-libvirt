import logging
import re

from avocado.core import exceptions
from avocado.utils import process

from virttest import utils_misc
from virttest import utils_test
from virttest import utils_vdpa

from virttest.libvirt_xml import vm_xml
from virttest.staging import service
from virttest.utils_libvirt import libvirt_vmxml

from provider.interface import interface_base
from provider.sriov import check_points

LOG = logging.getLogger('avocado.' + __name__)


def config_vdpa_conn(vm_session, vm_iface, br_name,
                     br_ip_addr='100.100.100.100', cidr='24'):
    """
    Config vdpa connection

    :param vm_session: An session to VM
    :param vm_iface: VM's interface
    :param br_name: Bridge name
    :param br_ip_addr: IP address of the bridge
    :param cidr: CIDR
    """
    vm_ip = re.sub('\d+$', '60', br_ip_addr)
    service.Factory.create_service("NetworkManager").stop()

    LOG.debug("Config static ip %s for vm.", vm_ip)
    cmd = ("nmcli con del {0}; nmcli con add type ethernet ifname {0} "
           "con-name {0} ipv4.method manual ipv4.address {1}/{2}"
           .format(vm_iface, vm_ip, cidr))
    vm_session.cmd(cmd)
    LOG.debug("Set ip address of the bridge.")
    cmd = ("ip addr del {0}/{1} dev {2}; sleep 5; ip addr add {0}/{1} dev {2};"
           "sleep 5;ip link set {2} up".format(br_ip_addr, cidr, br_name))
    process.run(cmd, shell=True)


def check_vdpa_network(vm_session, vm_iface, br_name,
                       ping_dest="100.100.100.100",
                       config_vdpa=True):
    """
    Check vdpa network connection

    :param vm_session: An session to VM
    :param vm_iface: VM's interface
    :param br_name: Bridge name
    :param ping_dest: The ip address to ping
    :config_vdpa: Whether to config vDPA connection
    """
    if config_vdpa:
        config_vdpa_conn(vm_session, vm_iface, br_name)

    if not utils_misc.wait_for(lambda: not utils_test.ping(
           ping_dest, count=3, timeout=5, output_func=logging.debug,
           session=vm_session)[0], first=5, timeout=30):
        raise exceptions.TestFail("Failed to ping %s." % ping_dest)


def check_rx_tx_packages(vm_session, vm_iface):
    """
    Check rx and tx package

    :param vm_session: An session to VM
    :param vm_iface: VM's interface
    """
    cmd = "ip -s -json link show %s" % vm_iface
    status, stdout = vm_session.cmd_status_output(cmd)
    if status or not stdout:
        raise exceptions.TestFail("Failed to run cmd - {}, status - {}, "
                                  "output - {}.".format(cmd, status, stdout))
    ip_info = eval(stdout.strip())
    LOG.debug("VM iface's info: %s.", ip_info)

    tx_info = ip_info[0]['stats64']['tx']['packets']
    rx_info = ip_info[0]['stats64']['rx']['packets']
    if rx_info != tx_info:
        raise exceptions.TestFail("The value of rx and tx should be same.")


def check_vdpa_conn(vm_session, test_target, br_name=None, config_vdpa=True):
    """
    Check vDPA connection

    :param vm_session: An session to VM
    :param test_target: Test target env, eg, "mellanox" or "simulator"
    :param br_name: Bridge name
    :config_vdpa: Whether to config vDPA connection
    """
    vm_iface = interface_base.get_vm_iface(vm_session)
    if test_target == "mellanox":
        # check_vdpa_network(vm_session, vm_iface, br_name,
        #                    config_vdpa=config_vdpa)
        # FIXME: Update to use check_vdpa_network when network topology changes
        check_points.check_vm_network_accessed(vm_session)

    elif test_target == "simulator":
        check_rx_tx_packages(vm_session, vm_iface)


def update_vm_disk_boot(vm_name, disk_boot):
    """
    Update boot order of vm's 1st disk before test

    :param vm_name: vm name
    :param disk_boot: boot order of disk
    """
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    vm_os = vmxml.os
    vm_os.del_boots()
    vmxml.os = vm_os
    disk = vmxml.get_devices('disk')[0]
    target_dev = disk.target.get('dev', '')
    LOG.debug('Set boot order %s to device %s', disk_boot, target_dev)
    vmxml.set_boot_order_by_target_dev(target_dev, disk_boot)
    vmxml.sync()


def get_iface_pci_id(vm_session):
    """
    Get pci id of VM's interface

    :param vm_session: VM session
    :return: pci id of VM's interface
    """
    cmd = "lspci | awk '/Eth/ {print $1}'"
    return vm_session.cmd_output(cmd).splitlines()[0]


def get_multiplier(vm_session, pci_id):
    """
    Get multiplier of VM's interface

    :param vm_session: VM session
    :param pci_id: The pci id
    :return: The multiplier of VM's interface
    """
    cmd = "lspci -vvv -s %s | awk -F '=' '/multiplier=/ {print $NF}'" % pci_id
    act_mul = vm_session.cmd_output(cmd).strip()
    LOG.debug("Actual multiplier: %s", act_mul)
    return act_mul


def setup_vdpa(vm, params):
    """
    Setup vDPA environment

    :param vm: VM object
    :param params: Dictionary with the test parameters
    :return: Object of vdpa test
    """
    LOG.debug("Remove VM's interface devices.")
    libvirt_vmxml.remove_vm_devices_by_type(vm, 'interface')
    vm_attrs = eval(params.get('vm_attrs', '{}'))
    if vm_attrs:
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm.name)
        vmxml.setup_attrs(**vm_attrs)
        vmxml.sync()
    disk_boot = params.get('disk_boot')
    if disk_boot:
        update_vm_disk_boot(vm.name, disk_boot)

    test_env_obj = None
    if params.get('test_target', '') == "simulator":
        test_env_obj = utils_vdpa.VDPASimulatorTest()
        test_env_obj.setup()
    else:
        vdpa_mgmt_tool_extra = params.get("vdpa_mgmt_tool_extra", "")
        pf_pci = utils_vdpa.get_vdpa_pci()
        test_env_obj = utils_vdpa.VDPAOvsTest(
            pf_pci, mgmt_tool_extra=vdpa_mgmt_tool_extra)
        test_env_obj.setup()
        params['mac_addr'] = test_env_obj.vdpa_mac.get(
            params.get("vdpa_dev", "vdpa0"))

    return test_env_obj, params


def cleanup_vdpa(test_target, test_obj):
    """
    Cleanup vDPA environment

    :param test_target: Test target, simulator or mellanox
    :param test_obj: Object of vdpa test
    """
    if test_target != "simulator":
        service.Factory.create_service("NetworkManager").restart()
    if test_obj:
        test_obj.cleanup()
