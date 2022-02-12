import logging as log
import time

from avocado.core import exceptions

from virttest import utils_net
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices import interface
from virttest.utils_libvirt import libvirt_misc
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def create_iface(iface_type, iface_dict):
    """
    Create Interface device

    :param iface_type: String, interface type
    :param iface_dict: Dict, attrs of Interface
    :return: xml object of interface
    """
    iface = interface.Interface(iface_type)
    iface.setup_attrs(**iface_dict)

    logging.debug("Interface XML: %s", iface)
    return iface


def get_vm_iface(vm_session):
    """
    Get VM's 1st interface

    :param vm_session: An session to VM
    :return: VM's first interface
    """
    p_iface, _v_ifc = utils_net.get_remote_host_net_ifs(vm_session)
    vm_iface = p_iface[:1:]
    if not vm_iface:
        raise exceptions.TestFail("Failed to get vm's iface!")
    return vm_iface[0]


def get_vm_iface_info(vm_session):
    """
    Get VM's interface info using 'ethtool' command

    :param vm_session: VM's session
    :return: Dict, vm's interface infomation
    """
    vm_iface = get_vm_iface(vm_session)
    cmd = 'ethtool -i %s' % vm_iface
    output_str = vm_session.cmd_output(cmd).strip()
    if_info = libvirt_misc.convert_to_dict(
        output_str, pattern=r'(\S+): +(\S+)')
    logging.debug("VM interface info: %s.", if_info)
    return if_info


def attach_iface_device(vm_name, dev_type, params):
    """
    Attach an interface to VM

    :param vm_name: VM's name
    :param dev_type: Interface device type
    :param params: Dictionary with the test parameters
    """
    iface_dict = eval(params.get('iface_dict', '{}'))
    status_error = "yes" == params.get('status_error', 'no')

    iface = create_iface(dev_type, iface_dict)
    res = virsh.attach_device(vm_name, iface.xml, debug=True)
    libvirt.check_exit_status(res, status_error)
    libvirt_vmxml.check_guest_xml(vm_name, dev_type)
    # FIXME: Sleep for 20 secs to make iface work properly
    time.sleep(20)


def detach_iface_device(vm_name, dev_type):
    """
    Detach an interface from VM

    :param vm_name: VM's name
    :param dev_type: Interface device type
    """
    iface = interface.Interface(dev_type)
    iface = vm_xml.VMXML.new_from_dumpxml(vm_name).devices.by_device_tag(
        "interface")[0]
    virsh.detach_device(vm_name, iface.xml, wait_for_event=True,
                        debug=True, ignore_status=False)
    libvirt_vmxml.check_guest_xml(vm_name, dev_type, status_error=True)
