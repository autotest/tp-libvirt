# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Nannan Li <nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import re

from avocado.utils import process

from virttest import libvirt_version
from virttest import virsh
from virttest.libvirt_xml import vm_xml

from provider.snapshot import snapshot_base
from provider.virtual_disk import disk_base

virsh_dargs = {"debug": True, "ignore_status": False}


def compare_value(test, actual_value, expected_value2):
    if actual_value != expected_value2:
        test.fail("Expect to get %s, but got %s" % (expected_value2, actual_value))


def check_existed(test, res, pattern):
    """
    Check if pattern exist in virsh.domblklist result.

    :param test: test object.
    :param res: the result.
    :param pattern: expected pattern.
    """
    search = re.findall(pattern, res)
    if not search:
        test.fail("%s not exist in %s" % (pattern, res))
    else:
        test.log.debug("Matched %s" % search)


def create_file(vm, file, session=None):
    """
    Create specific file in guest

    :param vm: vm object.
    :param file: the file to create.
    :param session: the guest session.
    """
    if not session:
        session = vm.wait_for_login()
    session.cmd("touch %s" % file)
    session.close()


def check_file_exist(test, vm, params, revert_snap):
    """
    Check file existed

    :param test: test object.
    :param vm: file list to be checked.
    :param params: Dictionary with the test parameters
    :param revert_snap: the reverted snapshot order.
    """
    vm_name = params.get("main_vm")

    if not vm.is_alive():
        virsh.start(vm_name)
    session = vm.wait_for_login()

    if revert_snap == "1":
        file_list = eval(params.get("file_list"))[0:1]
    elif revert_snap == "2":
        file_list = eval(params.get("file_list"))

    for file in file_list:
        status, _ = session.cmd_status_output("cat %s " % file)
        if status:
            test.fail("%s not exist" % file)
    session.close()


def update_xml(params, test, vm):
    """
    Update guest xml according to the scenarios.

    :param params: test parameters object
    :param test: test object
    :param vm: vm object
    """
    updated_type = params.get("updated_type")
    disk_dict = eval(params.get("disk_dict", "{}"))
    disk_type = params.get("disk_type")
    vm_name = params.get("main_vm")
    set_cpu = params.get("set_cpu")
    mode_cmd = params.get("mode_cmd")
    blkiotune_cmd = params.get("blkiotune_cmd")

    disk_obj = disk_base.DiskBase(test, vm, params)
    if updated_type == "hotplug_disk":
        disk, image_path = disk_obj.prepare_disk_obj(disk_type, disk_dict)
        virsh.attach_device(vm_name, disk.xml, **virsh_dargs)

    elif updated_type == "hotplug_vcpus":
        virsh.setvcpu(vm_name, set_cpu, "--enable", **virsh_dargs)

    elif updated_type == "blkiotune":
        process.run(mode_cmd, shell=True)

        res = process.run("lsscsi", shell=True).stdout_text.strip()
        params.update(
            {"dev": re.findall(r"Linux\s*scsi_debug\s*\d+\s+(\S+)\s*", res)[0]})
        virsh.blkiotune(vm_name, options=blkiotune_cmd % params.get("dev"),
                        **virsh_dargs)


def check_result_after_revert(params, test, vm, revert_snap):
    """
    Check the result after revert corresponding snapshot

    :param params: test parameters object
    :param test: test object
    :param vm: vm object
    :param revert_snap: the reverted snapshot order
    """
    vm_name = params.get("main_vm")
    updated_type = params.get("updated_type")
    original_vcpu = eval(params.get("original_vcpu", "{}"))
    new_vcpu = eval(params.get("new_vcpu", "{}"))
    system_disk_pattern = params.get("system_disk_pattern")
    new_disk_pattern = params.get("new_disk_pattern")
    blkiotune_weight = params.get("weight")

    if updated_type == "hotplug_disk":
        res = virsh.domblklist(vm_name, debug=True).stdout_text.strip()
        if revert_snap == "1":
            check_existed(test, res, system_disk_pattern)
        elif revert_snap == "2":
            check_existed(test, res, system_disk_pattern)
            check_existed(test, res, new_disk_pattern)

    elif updated_type == "hotplug_vcpus":
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        actual_vcpu = vmxml.vcpus.vcpu[1]
        if revert_snap == "1":
            compare_value(test, actual_vcpu["enabled"], original_vcpu["enabled"])
            compare_value(test, actual_vcpu["hotpluggable"], original_vcpu["hotpluggable"])
        elif revert_snap == "2":
            compare_value(test, actual_vcpu["enabled"], new_vcpu["enabled"])
            compare_value(test, actual_vcpu["hotpluggable"], new_vcpu["hotpluggable"])

    elif updated_type == "blkiotune":
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        blkiotune = vmxml.xmltreefile.find(".//blkiotune")
        if revert_snap == "1":
            compare_value(test, blkiotune != '', True)
        elif revert_snap == "2":
            compare_value(
                test,  blkiotune.find('.//device/write_bytes_sec').text,
                blkiotune_weight
            )
    check_file_exist(test, vm, params, revert_snap)


def run(test, params, env):
    """
    Revert snapshots after the guest xml is updated.
    """
    def setup_test():
        """
        Define guest if needed.
        """
        if updated_type == "hotplug_vcpus":
            vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
            vmxml.setup_attrs(**vm_attrs)
            virsh.define(vmxml.xml, **virsh_dargs)
            test.log.debug("New guest xml is:\n%s",
                           vm_xml.VMXML.new_from_inactive_dumpxml(vm_name))

    def run_test():
        """
        Revert snapshots after the guest xml is updated.
        Update guest xml scenario:
            hotplug disk/hotplug vcpus/blkiotune
        """
        test.log.info("TEST_STEP1:Prepare a running guest and create file.")
        virsh.start(vm_name)
        session = vm.wait_for_login()
        create_file(vm, file_list[0], session)

        test.log.info("TEST_STEP2: Create snapshot.")
        virsh.snapshot_create_as(
            vm_name, snap_options % (snap_names[0], snap_names[0],
                                     snap_names[0]), **virsh_dargs)
        test.log.info("TEST_STEP3: Update xml by %s and create file in guest." % updated_type)
        update_xml(params, test, vm)
        create_file(vm, file_list[1])

        test.log.info("TEST_STEP4: Create another snapshot after xml updated.")
        virsh.snapshot_create_as(
            vm_name, snap_options % (snap_names[1], snap_names[1],
                                     snap_names[1]), **virsh_dargs)

        test.log.info("TEST_STEP5: Revert to the 1st snapshot and check file.")
        virsh.snapshot_revert(vm_name, snap_names[0], **virsh_dargs)
        check_result_after_revert(params, test, vm, "1")

        test.log.info("TEST_STEP6: Revert to the 2nd snapshot and check file.")
        virsh.snapshot_revert(vm_name, snap_names[1], **virsh_dargs)
        check_result_after_revert(params, test, vm, "2")

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        snap_names.reverse()
        test_obj.teardown_test()

    vm_name = params.get("main_vm")
    original_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    params['backup_vmxml'] = original_xml.copy()
    vm = env.get_vm(vm_name)

    snap_names = eval(params.get("snap_names", '[]'))
    updated_type = params.get("updated_type")
    snap_options = params.get("snap_options")
    vm_attrs = eval(params.get("vm_attrs", "{}"))
    file_list = eval(params.get("file_list", '[]'))
    test_obj = snapshot_base.SnapshotTest(vm, test, params)

    libvirt_version.is_libvirt_feature_supported(params)

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
