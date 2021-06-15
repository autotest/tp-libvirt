import os
import aexpect

from avocado.utils import download

from virttest import data_dir
from virttest import utils_misc
from virttest import libvirt_version
from virttest import remote

from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml import xcepts
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Negative test for virtio/virtio-non-transitional model of disk
    """

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(params["main_vm"])
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()
    status_error = params['status_error']
    guest_src_url = params["guest_src_url"]
    image_name = params['image_path']
    target_path = utils_misc.get_path(data_dir.get_data_dir(), image_name)
    params["blk_source_name"] = target_path

    if not libvirt_version.version_compare(5, 0, 0):
        test.cancel("This libvirt version doesn't support "
                    "virtio-transitional model.")

    if not os.path.exists(target_path):
        download.get_file(guest_src_url, target_path)
    try:
        if (params["os_variant"] == 'rhel6' or
                'rhel6' in params.get("shortname")):
            iface_params = {'model': 'virtio-transitional'}
            libvirt.modify_vm_iface(vm_name, "update_iface", iface_params)
        try:
            libvirt.set_vm_disk(vm, params)
        except xcepts.LibvirtXMLError:
            if status_error == 'undefinable':
                return
            else:
                raise
        else:
            if status_error == 'undefinable':
                test.fail("Vm is expected to fail on defining with"
                          " invalid model, while it succeeds")
        try:
            if not vm.is_alive():
                vm.start()
            vm.wait_for_serial_login()
        except (remote.LoginTimeoutError, aexpect.ExpectError):
            pass
        else:
            test.fail("Vm is expected to fail on booting from disk"
                      " with wrong model, while login successfully.")

    finally:
        backup_xml.sync()
