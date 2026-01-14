import os
import re
import ast

from avocado.utils import process

from virttest import data_dir
from virttest import libvirt_version
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt

from provider.snapshot import snapshot_base
from provider.virtual_disk import disk_base

virsh_dargs = {"debug": True, "ignore_status": False}


def prepare_guest(params, test, disk_obj):
    """
    Prepare guest with additional disks.

    :param params: dict, test parameters
    :param test: test object
    :param disk_obj: disk object for adding disks to VM
    :return: dict of disk paths {disk_name: disk_path}
    """
    image_size = params.get("image_size")
    disk_type = params.get("disk_type", "file")
    disk_list = ast.literal_eval(params.get("disk_list", "[]"))
    vm_name = params.get("main_vm")
    disk_paths = {}

    vm_xml_obj = vm_xml.VMXML.new_from_dumpxml(vm_name)
    disk_sources = disk_base.DiskBase.get_source_list(vm_xml_obj, "file", "vda")
    disk_paths["vda"] = disk_sources[0] if disk_sources else None
    disk_paths["vdb"] = os.path.join(data_dir.get_tmp_dir(), "test1.qcow2")
    disk_paths["vdc"] = os.path.join(data_dir.get_tmp_dir(), "test2.qcow2")

    for disk_name in ["vdb", "vdc"]:
        img_path = disk_paths[disk_name]
        libvirt.create_local_disk(disk_type, path=img_path, size=image_size, disk_format="qcow2")
        test.log.debug(f"Created image {img_path} with size {image_size}")

    for target_disk in disk_list:
        if target_disk == "vda":
            continue
        disk_dict = ast.literal_eval(params.get("disk_dict") % target_disk)
        disk_obj.add_vm_disk(disk_type, disk_dict, disk_paths[target_disk])

    return disk_paths


def create_snapshots(params, test):
    """
    Create manual snapshots with diskspec options.

    :param params: dict, test parameters
    :param test: test object
    """
    test.log.info("TEST_STEP3: Create snapshots based on the snapshot command.")
    vm_name = params.get("main_vm")
    snap_name = params.get("snap_name")
    snap_options = params.get("snap_options")
    diskspec_options = params.get("diskspec_options")

    options_parts = [snap_name, snap_options, diskspec_options]
    full_snap_options = " ".join(part for part in options_parts if part)
    virsh.snapshot_create_as(vm_name, full_snap_options, **virsh_dargs)
    test.log.debug(f"Created snapshot {snap_name} with options: {full_snap_options}")


def check_bitmap_auto_flag(result, checkpoint_name):
    """
    Check if bitmap has auto flag in qemu-img info output.

    :param result: qemu-img info output string
    :param checkpoint_name: name of the checkpoint to check
    :return: True if auto flag found, False otherwise
    """
    lines = result.split('\n')
    in_bitmaps_section = False
    current_bitmap_has_auto = False
    current_bitmap_name = None

    for line in lines:
        line = line.strip()

        # Check if we're in the bitmaps section
        if line == "bitmaps:":
            in_bitmaps_section = True
            continue

        # If not in bitmaps section, skip
        if not in_bitmaps_section:
            continue

        # Check for start of a new bitmap entry
        if re.match(r'^\[\d+\]:$', line):
            # Reset for new bitmap
            current_bitmap_has_auto = False
            current_bitmap_name = None
        # Check for auto flag
        elif "auto" in line:
            current_bitmap_has_auto = True
        # Check for bitmap name
        elif line.startswith("name:"):
            name_value = line.split("name:", 1)[1].strip()
            current_bitmap_name = name_value
            # If this is the target checkpoint and it has auto flag, return True
            if current_bitmap_name == checkpoint_name and current_bitmap_has_auto:
                return True
    return False


def check_image_info(params, test, disk_list, disk_paths):
    """
    Check test results for bitmap flags in qemu images.

    Expected results:
    - only_one_manual=yes: Only for vdb and vdc, the bitmaps flags are auto in the result of qemu-img info
    - with_multiple_manual=yes: For vda, vdb and vdc, the bitmaps flags are auto in the result of qemu-img info

    :param params: dict, test parameters
    :param test: test object
    :param disk_list: list of disk names (used as fallback if test variant not determined)
    :param disk_paths: dict of disk paths {disk_name: disk_path}
    """
    checkpoint_name = params.get("checkpoint_name")
    only_one_manual = params.get("only_one_manual", "no") == "yes"
    with_multiple_manual = params.get_boolean("with_multiple_manual")

    # Determine expected disks with auto bitmap flags based on test variant
    if only_one_manual:
        expected_auto_disks = disk_list[1:]
    elif with_multiple_manual:
        expected_auto_disks = disk_list
    else:
        expected_auto_disks = []

    for disk in expected_auto_disks:
        img_path = disk_paths.get(disk)
        if not img_path:
            test.fail(f"Could not find path for disk {disk} in disk_paths")
        cmd = f"qemu-img info {img_path} -U"
        result = process.run(cmd, ignore_status=False).stdout_text
        test.log.debug(f"Image info for {disk} ({img_path}): {result}")

        bitmap_found = checkpoint_name in result
        if not bitmap_found:
            test.fail(f"Expected checkpoint {checkpoint_name} in image info for disk {disk}, but not found")

        auto_flag_found = check_bitmap_auto_flag(result, checkpoint_name)
        if not auto_flag_found:
            test.fail(f"Expected 'auto' flag in bitmap for disk {disk}, but not found")
        else:
            test.log.info(f"For disk {disk}: Found expected 'auto' flag in bitmap")

    test.log.info("Test results validation completed successfully")


def check_test_result(vm, params, test, disk_paths):
    """
    Check test results including bitmap flags and VM state transitions.

    :param vm: vm object
    :param params: dict, test parameters
    :param test: test object
    :param disk_paths: dict of disk paths {disk_name: disk_path}
    """
    test.log.info("TEST_STEP4: Check the test results for bitmap flags.")
    vm_name = params.get("main_vm")
    disk_list = ast.literal_eval(params.get("disk_list", "[]"))

    check_image_info(params, test, disk_list, disk_paths)
    test.log.info("TEST_STEP5: Check the guest status (should be paused).")
    if not libvirt.check_vm_state(vm_name, "paused"):
        test.fail(f"Expected VM to be paused after snapshot, but got state: {vm.state()}")
    test.log.debug("VM is correctly paused after snapshot creation")

    test.log.info("TEST_STEP6: Resume the guest and check the status again.")
    virsh.resume(vm_name, **virsh_dargs)

    if not libvirt.check_vm_state(vm_name, "running"):
        test.fail(f"Expected VM to be running after resume, but got state: {vm.state()}")
    test.log.debug("VM is correctly running after resume")


def run(test, params, env):
    """
    Create manual snapshot test.
    """
    def run_test():
        """
        Create manual snapshot test.
        """
        nonlocal disk_paths
        test.log.info("TEST_STEP1: Create 2 new images and start guest with them.")
        disk_paths = prepare_guest(params, test, disk_obj)
        if not vm.is_alive():
            virsh.start(vm_name, **virsh_dargs)
        vm.wait_for_login().close()

        test.log.info("TEST_STEP2: Create a checkpoint and restart guest.")
        virsh.checkpoint_create_as(vm_name, checkpoint_name, **virsh_dargs)
        test.log.debug(f"Created checkpoint {checkpoint_name}")

        virsh.destroy(vm_name, **virsh_dargs)
        virsh.start(vm_name, **virsh_dargs)
        vm.wait_for_login().close()

        create_snapshots(params, test)
        check_test_result(vm, params, test, disk_paths)

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        try:
            checkpoints = virsh.checkpoint_list(vm_name)
            if checkpoint_name in checkpoints.stdout:
                virsh.checkpoint_delete(vm_name, checkpoint_name)
        except Exception as e:
            test.log.debug(f"Error cleaning checkpoint: {e}")

        test_disks = disk_list[1:]
        for disk_name in test_disks:
            img_path = disk_paths.get(disk_name)
            if img_path and os.path.exists(img_path):
                try:
                    os.remove(img_path)
                except Exception as e:
                    test.log.debug(f"Error removing image {img_path}: {e}")
        test_obj.teardown_test()

    libvirt_version.is_libvirt_feature_supported(params)
    vm_name = params.get("main_vm")
    checkpoint_name = params.get("checkpoint_name")
    disk_list = ast.literal_eval(params.get("disk_list", "[]"))
    original_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    params['backup_vmxml'] = original_xml.copy()
    vm = env.get_vm(vm_name)

    test_obj = snapshot_base.SnapshotTest(vm, test, params)
    disk_obj = disk_base.DiskBase(test, vm, params)
    disk_paths = {}

    try:
        run_test()

    finally:
        teardown_test()
