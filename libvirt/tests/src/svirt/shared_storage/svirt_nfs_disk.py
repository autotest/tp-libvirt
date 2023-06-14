import os

from virttest import data_dir
from virttest import virsh

from virttest.libvirt_xml.vm_xml import VMXML
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Test svirt works correctly for nfs storage.
    """
    status_error = 'yes' == params.get("status_error", 'no')

    # Get variables about VM and get a VM object and VMXML instance.
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vmxml = VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()

    try:
        test.log.info("TEST_STEP: Update disk xml.")
        disk_img = os.path.basename(vm.get_first_disk_devices()['source'])
        disk_dict = {'source': {'attrs': {'file': os.path.join(
            params.get("nfs_mount_dir"), disk_img)}}}
        libvirt_vmxml.modify_vm_device(vmxml, 'disk', disk_dict)
        test.log.debug(f"VM xml after updating disk: {VMXML.new_from_dumpxml(vm_name)}")

        test.log.info("TEST_STEP: Start the VM.")
        if vm.is_alive():
            vm.destroy()
        res = virsh.start(vm.name, debug=True)
        libvirt.check_exit_status(res, status_error)
        if status_error:
            return

        test.log.info("TEST_STEP: Save and restore the VM.")
        save_path = os.path.join(data_dir.get_tmp_dir(), 'test.save')
        virsh.save(vm_name, save_path, debug=True, ignore_status=False)
        virsh.restore(save_path, debug=True, ignore_status=False)
    finally:
        test.log.info("TEST_TEARDOWN: Recover test environment.")
        backup_xml.sync()
