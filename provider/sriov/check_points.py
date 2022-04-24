import logging
import re

from avocado.core import exceptions
from avocado.utils import process

from virttest import utils_net
from virttest import virsh
from virttest.libvirt_xml import vm_xml


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
    iface = utils_net.get_linux_iface_info(mac, vm_session)
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
