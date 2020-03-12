import os
import re
import logging

from avocado.utils import download

from virttest import remote
from virttest import data_dir
from virttest import utils_misc
from virttest import libvirt_version

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

    if not libvirt_version.version_compare(5, 0, 0):
        test.cancel("This libvirt version doesn't support "
                    "virtio-transitional model.")

    if not os.path.exists(target_path):
        download.get_file(guest_src_url, target_path)
        params["blk_source_name"] = target_path
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
        except remote.LoginTimeoutError:
            pass
        else:
            test.fail("Vm is expected to fail on booting from disk"
                      " with wrong model, while login successfully.")
        data = vm.serial_console.get_output()
        if data is None or len(data.splitlines()) < 5:
            logging.warn(
                "Unable to read serial console or no sufficient data in"
                " serial console output to detect the kernel panic.")
        else:
            match = re.search('Kernel panic', data, re.S | re.M | re.I)
            if not match:
                test.fail("Can not find 'Kernel panic' keyword in"
                          " serial console output.")
    finally:
        backup_xml.sync()
