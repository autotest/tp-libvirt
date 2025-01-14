import logging
import platform

from avocado.core import exceptions
from avocado.utils import distro
from avocado.utils import download
from avocado.utils import process

from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_bios
from virttest.utils_test import libvirt


LOG = logging.getLogger('avocado.' + __name__)


def get_vm(params):
    """
    Get VM by given test parameters

    :param params: Dictionary with the test parameters
    :return: Name of VM
    """
    vms = params.get('vms').split()
    firmware_type = params.get('firmware_type')
    detected_distro = distro.detect()
    os_type_dict = {}
    if platform.machine() == "s390x":
        return vms[0]

    for _vm in vms:
        if os_type_dict.get("seabios") and os_type_dict.get("ovmf"):
            break
        vm_os_attrs = vm_xml.VMXML.new_from_dumpxml(_vm).os.fetch_attrs()
        is_ovmf = any([vm_os_attrs.get('os_firmware') == "efi", vm_os_attrs.get('nvram')])
        if is_ovmf:
            if not os_type_dict.get("ovmf"):
                os_type_dict["ovmf"] = _vm
        elif not os_type_dict.get("seabios"):
            os_type_dict["seabios"] = _vm
    LOG.debug(f"VMS: {os_type_dict}")
    if not firmware_type:
        LOG.debug("Get default vm")
        if detected_distro.name == 'rhel':
            if int(detected_distro.version) < 9:
                firmware_type = 'seabios'
            else:
                firmware_type = 'ovmf'
        else:
            return vms[0]

    vm_res = os_type_dict.get(firmware_type)
    if not vm_res:
        raise exceptions.TestError("Unable to get vm!")
    return vm_res


def prepare_os_xml(vm_name, os_dict, firmware_type=None):
    """
    Prepare a guest with related os loader xml.

    :params vm_name: the name of guest
    :params os_dict: the dict of os elements
    :params firmware_type: seabios or ovmf
    """
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    if firmware_type == "ovmf":
        vmxml.os = libvirt_bios.remove_bootconfig_items_from_vmos(vmxml.os)
    LOG.debug("Set the os xml")
    vmxml.setup_attrs(os=os_dict)
    vmxml.sync()
    virsh.dumpxml(vm_name, debug=True)
    return vmxml


def prepare_smm_xml(vm_name, smm_state, smm_size):
    """
    Prepare a guest with related feature smm xml.

    :params vm_name: the name of guest
    :params smm_state: the state of smm
    :params smm_size: the size of smm tseg
    """
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    LOG.debug("Set the smm element in feature xml")
    feature_xml = vmxml.features
    feature_xml.smm = smm_state
    if smm_size:
        feature_xml.smm_tseg = smm_size
    vmxml.features = feature_xml
    vmxml.sync()
    virsh.dumpxml(vm_name, debug=True)
    return vmxml


def check_vm_startup(vm, vm_name, error_msg=None):
    """
    Start and boot the guest

    :params vm: vm object
    :params vm_name: the name of guest
    """
    ret = virsh.start(vm_name, timeout=30, debug=True)
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    libvirt.check_result(ret, expected_fails=error_msg)
    if not error_msg:
        vm.wait_for_login().close()
        LOG.debug("Succeed to boot %s", vm_name)
    return vmxml


def test_file_download(url, path):
    """
    Returns true if the file could be successfully downloaded

    :param url: source URL
    :param path: destination path
    """
    download.get_file(url, path)
    return process.run('ls -d ' + path, ignore_status="yes").exit_status == 0
