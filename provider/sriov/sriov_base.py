import re

from avocado.core import exceptions
from avocado.utils import process

from virttest import utils_misc
from virttest import utils_net
from virttest import utils_package
from virttest import utils_sriov
from virttest import utils_test


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
    cmd = ("ip route |awk -F '/' '/^[0-9]/, /dev %s/ {print $1}' |tail -1"
           % iface_name)
    status, output = utils_misc.cmd_status_output(cmd, shell=True,
                                                  session=vm_session)
    if status or not output:
        raise exceptions.TestError("Failed to run cmd - {}, status - {}, "
                                   "output - {}.".format(cmd, status, output))
    return re.sub('\d+$', '1', output.strip())


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
