import logging
import re

from avocado.core import exceptions
from avocado.utils import process

from provider.interface import interface_base

from virttest import utils_test
from virttest import utils_misc
from virttest.staging import service


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

    logging.debug("Config static ip %s for vm.", vm_ip)
    cmd = ("nmcli con del {0}; nmcli con add type ethernet ifname {0} "
           "con-name {0} ipv4.method manual ipv4.address {1}/{2}"
           .format(vm_iface, vm_ip, cidr))
    vm_session.cmd(cmd)
    logging.debug("Set ip address of the bridge.")
    cmd = ("ip addr add {0}/{1} dev {2}; sleep 5;ip link set {2} up"
           .format(br_ip_addr, cidr, br_name))
    process.run(cmd, shell=True)


def check_vdpa_network(vm_session, vm_iface, br_name,
                       ping_dest="100.100.100.100"):
    """
    Check vdpa network connection

    :param vm_session: An session to VM
    :param vm_iface: VM's interface
    :param br_name: Bridge name
    :param ping_dest: The ip address to ping
    """
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
    logging.debug("VM iface's info: %s.", ip_info)

    tx_info = ip_info[0]['stats64']['tx']['packets']
    rx_info = ip_info[0]['stats64']['rx']['packets']
    if rx_info != tx_info:
        raise exceptions.TestFail("The value of rx and tx should be same.")


def check_vdpa_conn(vm_session, test_target, br_name=None):
    """
    Check vDPA connection

    :param vm_session: An session to VM
    :param test_target: Test target env, eg, "mellanox" or "simulator"
    :param br_name: Bridge name
    """
    vm_iface = interface_base.get_vm_iface(vm_session)
    if test_target == "mellanox":
        check_vdpa_network(vm_session, vm_iface, br_name)
    elif test_target == "simulator":
        check_rx_tx_packages(vm_session, vm_iface)
