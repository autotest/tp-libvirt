import os
import logging

from avocado.utils import download

from virttest import virsh
from virttest import data_dir
from virttest import utils_misc
from virttest import libvirt_version

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Test virtio/virtio-transitional/virtio-non-transitional model of
    memory balloon

    :param test: Test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment

    Test steps:
    1. Prepareguest domain and balloon xml use one of
    virtio/virtio-non-transitional/virtio-transitional model
    2. Start domain and check the device exist in guest
    3. Save/Restore and check if guest works well
    4. Set balloon memory and see if it works in guest
    """

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(params["main_vm"])
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()
    guest_src_url = params.get("guest_src_url")
    virtio_model = params['virtio_model']
    os_variant = params.get("os_variant", "")
    params["disk_model"] = virtio_model

    if not libvirt_version.version_compare(5, 0, 0):
        test.cancel("This libvirt version doesn't support "
                    "virtio-transitional model.")

    # Download and replace image when guest_src_url provided
    if guest_src_url:
        image_name = params['image_path']
        target_path = utils_misc.get_path(data_dir.get_data_dir(), image_name)
        if not os.path.exists(target_path):
            download.get_file(guest_src_url, target_path)
        params["blk_source_name"] = target_path

    try:
        # Update disk and interface to correct model
        if (os_variant == 'rhel6' or
                'rhel6' in params.get("shortname")):
            iface_params = {'model': 'virtio-transitional'}
            libvirt.modify_vm_iface(vm_name, "update_iface", iface_params)
        libvirt.set_vm_disk(vm, params)
        # The local variable "vmxml" will not be updated since set_vm_disk
        # sync with another dumped xml inside the function
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        # Update memory balloon device to correct model
        membal_dict = {'membal_model': virtio_model,
                       'membal_stats_period': '10'}
        libvirt.update_memballoon_xml(vmxml, membal_dict)
        if not vm.is_alive():
            vm.start()
        is_windows_guest = (params['os_type'] == 'Windows')
        session = vm.wait_for_login()
        # Check memory statistic
        if libvirt_version.version_compare(6, 6, 0):
            if (os_variant != 'rhel6' or
                    'rhel6' not in params.get("shortname")):
                rs = virsh.dommemstat(vm_name, ignore_status=True,
                                      debug=True).stdout_text
                if "available" not in rs:
                    test.fail("Can't get memory stats in %s model" % virtio_model)
        # Finish test for Windows guest
        if is_windows_guest:
            return
        # Check if memory balloon device exists on guest
        status = session.cmd_status_output('lspci |grep balloon')[0]
        if status != 0:
            test.fail("Didn't detect memory balloon device on guest.")
        # Save and restore guest
        sn_path = os.path.join(data_dir.get_tmp_dir(), os_variant)
        session.close()
        virsh.save(vm_name, sn_path)
        virsh.restore(sn_path)
        session = vm.wait_for_login()
        # Get original memory for later balloon function check
        ori_outside_mem = vm.get_max_mem()
        ori_guest_mem = vm.get_current_memory_size()
        # balloon half of the memory
        ballooned_mem = ori_outside_mem // 2
        # Set memory to test balloon function
        virsh.setmem(vm_name, ballooned_mem)
        # Check if memory is ballooned successfully
        logging.info("Check memory status")
        unusable_mem = ori_outside_mem - ori_guest_mem
        gcompare_threshold = int(
            params.get("guest_compare_threshold", unusable_mem))
        after_mem = vm.get_current_memory_size()
        act_threshold = ballooned_mem - after_mem
        if (after_mem > ballooned_mem) or (
                abs(act_threshold) > gcompare_threshold):
            test.fail("Balloon test failed")
    finally:
        vm.destroy()
        backup_xml.sync()
