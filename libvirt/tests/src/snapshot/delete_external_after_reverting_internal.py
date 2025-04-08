# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Liang Cong <lcong@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import os

from virttest import data_dir
from virttest import libvirt_version
from virttest import utils_misc
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.guest_os_booting import guest_os_booting_base


def run(test, params, env):
    """
    Delete external snapshot after reverting to internal snapshot.
    """
    def check_dom_disks():
        """
        Check domain disk type and file type disk number.
        """
        domxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        disks = domxml.get_disk_all_by_expr("type!=file", "device==disk")
        if disks:
            test.error("Unsupported non-file type disk found in domain.")
        disks = domxml.get_disk_all_by_expr("type==file", "device==disk")
        if len(disks) != 1:
            test.error("Expected only 1 file type disk but found %s." % len(disks))

    def check_internal_snap():
        """
        Check internal snapshot info.
        """
        snap_info = virsh.snapshot_info(vm_name, internal_snap_name, **virsh_dargs)
        if snap_info["Name"] != internal_snap_name or snap_info["Location"] != "internal":
            test.fail("Expected internal snapshot named after '%s', but found %s snapshot named after '%s'." % (
                internal_snap_name, snap_info["Location"], snap_info["Name"]))
        internal_snap_list = utils_misc.get_image_snapshot(disk_source)
        if len(internal_snap_list) != 1:
            test.fail("Expected only 1 internal snapshot in image, but found %s." % len(
                internal_snap_list))

    def check_disk_xml(xpath_list):
        """
        Check domain disk xml according to xpath.

        :param xpath_list: expected xpath list.
        """
        dom_xml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        test.log.debug("domain xml is: %s", dom_xml)
        libvirt_vmxml.check_guest_xml_by_xpaths(dom_xml, xpath_list)

    def check_file_after_snapshot_delete(file, exist):
        """
        Check snapshot related file after snapshot delete.

        :param file: str, snapshot related file path.
        :param exist: bool, file should exist or not.
        """
        if os.path.exists(file) != exist:
            judgement_string = "" if exist else "not "
            test.fail("file '%s' should %sexist after snapshot delete." % (file, judgement_string))

    def run_test():
        """
        1. Define a guest.
        2. Create an internal snapshot.
        3. Start the guest.
        4. Create an external snapshot.
        5. Revert snapshot to the internal snapshot.
        6. Delete the external snapshot.
        7. Check the snapshot related images.
        """
        #since default guest is used, so step 1 virsh define is skipped.
        test.log.info("TEST_STEP2:Create an internal snapshot.")
        virsh.snapshot_create_as(vm_name, internal_snap_option, **virsh_dargs)
        check_internal_snap()

        test.log.info("TEST_STEP3:Start the guest.")
        vm.start()
        vm.wait_for_login().close()

        test.log.info("TEST_STEP4:Create an external snapshot.")
        virsh.snapshot_create_as(vm_name, external_snap_option, **virsh_dargs)
        check_disk_xml(disk_xpath)

        test.log.info("TEST_STEP5:Revert snapshot to the internal snapshot.")
        virsh.snapshot_revert(vm_name, internal_snap_name, **virsh_dargs)
        dom_xml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        current_disk_dict = dom_xml.devices.by_device_tag('disk')[0].fetch_attrs()
        original_disk_dict = original_disk.fetch_attrs()
        if original_disk_dict != current_disk_dict:
            test.fail("Expected domain disk is %s, but found %s." %
                      (original_disk_dict, current_disk_dict))

        test.log.info("TEST_STEP6:Delete the external snapshot.")
        virsh.snapshot_delete(vm_name, external_snap_name, **virsh_dargs)

        test.log.info("TEST_STEP7:Check the snapshot related images.")
        for snap_file in external_snap_file_list:
            check_file_after_snapshot_delete(snap_file, exist=False)
        check_file_after_snapshot_delete(disk_source, exist=True)

    def teardown_test():
        """
        Clean test environment.
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        if vm.is_alive():
            vm.destroy()
        libvirt.clean_up_snapshots(vm_name)
        for snap_file in external_snap_file_list:
            if os.path.exists(snap_file):
                os.remove(snap_file)
        undefine_option = "--nvram" if firmware_type == "ovmf" else None
        bkxml.sync(undefine_option)

    libvirt_version.is_libvirt_feature_supported(params)
    vm_name = guest_os_booting_base.get_vm(params)
    firmware_type = params.get("firmware_type")
    internal_snap_name = params.get("internal_snap_name")
    external_snap_name = params.get("external_snap_name")
    external_mem_file = "%s/%s.%s" % (data_dir.get_tmp_dir(), vm_name, external_snap_name)
    internal_snap_option = params.get("internal_snap_option")
    external_snap_option = params.get("external_snap_option") % external_mem_file

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    original_disk = vmxml.devices.by_device_tag('disk')[0]
    vm = env.get_vm(vm_name)
    disk_source = vm.get_first_disk_devices()["source"]
    ori_image_file, _ = os.path.splitext(disk_source)
    external_snapshot_file = "%s.%s" % (ori_image_file, external_snap_name)
    external_snap_file_list = [external_mem_file, external_snapshot_file]
    disk_xpath = eval(params.get("disk_xpath") % (external_snapshot_file, disk_source))
    virsh_dargs = {"debug": True, "ignore_status": False}

    try:
        check_dom_disks()
        run_test()

    finally:
        teardown_test()
