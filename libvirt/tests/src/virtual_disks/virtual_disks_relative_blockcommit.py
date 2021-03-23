"""
Module to test cases for virsh blockcommit with relative path.

This module is meant to wrap test cases to validate virsh blockcommit with relative path.
:author: Chunfu Wen <chwen@redhat.com>
:copyright: 2021 Red Hat Inc.
"""

import logging
import os
import time
import threading


from avocado.utils import process

from virttest import data_dir
from virttest import virsh
from virttest import utils_libvirtd
from virttest import utils_disk

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt

from virttest.utils_libvirt import libvirt_ceph_utils
from virttest.utils_libvirt import libvirt_disk


def run(test, params, env):
    """
    Test scenarios: virsh blockcommit with relative path

    1) Prepare test environment.
    2) Create relative path backing chain
    3) Do virsh blockcommit
    4) Check result.
    5) Recover the environments
    """
    def check_chain_backing_files(disk_src_file, expect_backing_list):
        """
        Check backing chain files of relative path after blockcommit.

        :param disk_src_file: first disk src file.
        :param expect_backing_list: backing chain lists.
        """
        # Validate source image doesn't have backing files after active blockcommit
        qemu_img_info_backing_chain = libvirt_disk.get_chain_backing_files(disk_src_file)
        logging.debug("The actual qemu-img qemu_img_info_backing_chain:%s\n", qemu_img_info_backing_chain)
        logging.debug("The actual qemu-img expect_backing_list:%s\n", expect_backing_list)
        if qemu_img_info_backing_chain != expect_backing_list:
            test.fail("The backing files by qemu-img is not identical in expected backing list")

    def check_top_image_in_xml(expected_top_image):
        """
        check top image in src file

        :param expected_top_image: expect top image
        """
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        disks = vmxml.devices.by_device_tag('disk')
        disk_xml = None
        for disk in disks:
            if disk.target['dev'] == disk_target:
                disk_xml = disk.xmltreefile
                break
        logging.debug("disk xml in top: %s\n", disk_xml)
        for attr in ['file', 'name', 'dev']:
            src_file = disk_xml.find('source').get(attr)
            if src_file:
                break
        if src_file not in expected_top_image:
            test.fail("Current top img %s is not the same with expected: %s" % (src_file, expected_top_image))

    def check_blockcommit_with_bandwidth(chain_list):
        """
        Check blockcommit with bandwidth

        param chain_list: list, expected backing chain list
        """
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        disks = vmxml.devices.by_device_tag('disk')
        disk_xml = None
        for disk in disks:
            if disk.target['dev'] == disk_target:
                disk_xml = disk
                break
        logging.debug("disk xml in check_blockcommit_with_bandwidth: %s\n", disk_xml.xmltreefile)
        backingstore_list = disk_xml.get_backingstore_list()
        parse_source_file_list = [elem.find('source').get('file') or elem.find('source').get('name') for elem in backingstore_list]

        logging.debug("expected backing chain list is %s", chain_list)
        logging.debug("parse source list is %s", parse_source_file_list)
        # Check whether relative path has been kept
        for i in range(0, len(chain_list)-1):
            if chain_list[i] not in parse_source_file_list[i]:
                test.fail("The relative path parsed from disk xml is diffrent with pre-expected ones")

    def check_file_not_exists(root_dir, file_name, reverse=False):
        """
        Check whether file exists in certain folder

        :param root_dir: preset root directory
        :param file_name:  input file name
        :param reverse: whether reverse the condition
        """
        files_path = [os.path.join(root_dir, f) for f in os.listdir(root_dir)
                      if os.path.isfile(os.path.join(root_dir, f))
                      ]
        logging.debug("all files in folder: %s \n", files_path)
        if not files_path:
            test.fail("Failed to get snapshot files in preset folder")
        elif reverse:
            if file_name not in files_path:
                test.fail("snapshot file:%s can not be found" % file_name)
        else:
            if file_name in files_path:
                test.fail("snapshot file:%s  can not be deleted" % file_name)

    def check_backing_chain_file_not_exists(disk_src_file, file_name, reverse=False):
        """
        Check whether file exists in source file's backing chain

        :param disk_src_file: disk source with backing chain files
        :param file_name: input file name
        :param reverse: whether reverse this condition
        """
        qemu_img_info_backing_chain = libvirt_disk.get_chain_backing_files(disk_src_file)
        if reverse:
            if file_name not in qemu_img_info_backing_chain:
                test.fail("%s can not be found in backing chain file" % file_name)
        else:
            if file_name in qemu_img_info_backing_chain:
                test.fail("%s should not be in backing chain file" % file_name)

    def fill_vm_with_contents():
        """ Fill contents in VM """
        logging.info("Filling VM contents...")
        try:
            session = vm.wait_for_login()
            status, output = session.cmd_status_output(
                "dd if=/dev/urandom of=/tmp/bigfile bs=1M count=200")
            logging.info("Fill contents in VM:\n%s", output)
            session.close()
        except Exception as e:
            logging.error(str(e))

    def create_lvm_pool():
        """ create lvm pool"""
        pvt.cleanup_pool(pool_name, pool_type, pool_target, emulated_image)
        pvt.pre_pool(**params)
        capacity = "5G"
        for i in range(1, 5):
            vol_name = 'vol%s' % i
            path = "%s/%s" % (pool_target, vol_name)
            virsh.vol_create_as(vol_name, pool_name, capacity, capacity, "qcow2", debug=True)
            cmd = "qemu-img create -f %s %s %s" % ("qcow2", path, capacity)
            process.run(cmd, ignore_status=False, shell=True)
            volume_path_list.append(path)
            capacity = "2G"

    def setup_iscsi_env():
        """ Setup iscsi environment"""
        libvirt.setup_or_cleanup_iscsi(is_setup=False)
        emulated_size = params.get("image_size", "10G")
        iscsi_target, lun_num = libvirt.setup_or_cleanup_iscsi(is_setup=True,
                                                               is_login=False,
                                                               image_size=emulated_size,
                                                               portal_ip="127.0.0.1")
        cmd = ("qemu-img create -f qcow2 iscsi://%s:%s/%s/%s %s"
               % ("127.0.0.1", "3260", iscsi_target, lun_num, emulated_size))
        process.run(cmd, shell=True)

        blk_source_image_after_converted = "iscsi://%s:%s/%s/%s" % ("127.0.0.1", "3260", iscsi_target, lun_num)
        # Convert the image from qcow2 to raw
        convert_disk_cmd = ("qemu-img convert"
                            " -O %s %s %s" % (disk_format, first_src_file, blk_source_image_after_converted))
        process.run(convert_disk_cmd, ignore_status=False, shell=True)

        replace_disk_image, backing_chain_list = libvirt_disk.make_relative_path_backing_files(
            vm, pre_set_root_dir, blk_source_image_after_converted, disk_format)
        params.update({'disk_source_name': replace_disk_image,
                       'disk_type': 'file',
                       'disk_source_protocol': 'file'})
        return replace_disk_image, blk_source_image_after_converted, backing_chain_list

    def setup_rbd_env():
        """ Set up rbd environment"""
        params.update(
            {"virt_disk_device_target": disk_target,
             "ceph_image_file": first_src_file})
        libvirt_ceph_utils.create_or_cleanup_ceph_backend_vm_disk(vm, params, is_setup=True)
        ceph_mon_ip = params.get("ceph_mon_ip", "EXAMPLE_MON_HOST")
        ceph_disk_name = params.get("ceph_disk_name", "EXAMPLE_SOURCE_NAME")
        blk_source_image_after_converted = ("rbd:%s:mon_host=%s" %
                                            (ceph_disk_name, ceph_mon_ip))
        replace_disk_image, backing_chain_list = libvirt_disk.make_relative_path_backing_files(
            vm, pre_set_root_dir, blk_source_image_after_converted, disk_format)
        params.update({'disk_source_name': replace_disk_image,
                       'disk_type': 'file',
                       'disk_format': 'qcow2',
                       'disk_source_protocol': 'file'})
        return replace_disk_image, blk_source_image_after_converted, backing_chain_list

    def setup_volume_pool_env():
        """Setup volume pool environment"""
        params.update(
            {"virt_disk_device_target": disk_target})
        create_lvm_pool()

        blk_source_image_after_converted = ("%s" % volume_path_list[0])
        # Convert the image from qcow2 to volume
        convert_disk_cmd = ("qemu-img convert"
                            " -O %s %s %s" % (disk_format, first_src_file, blk_source_image_after_converted))
        process.run(convert_disk_cmd, ignore_status=False, shell=True)
        params.update({'disk_source_name': blk_source_image_after_converted,
                       'disk_type': 'block',
                       'disk_format': 'qcow2',
                       'disk_source_protocol': 'file'})
        libvirt.set_vm_disk(vm, params, tmp_dir)
        vm.wait_for_login().close()
        vm.destroy(gracefully=False)
        replace_disk_image, backing_chain_list = libvirt_disk.make_syslink_path_backing_files(
            pre_set_root_dir, volume_path_list, disk_format)
        params.update({'disk_source_name': replace_disk_image,
                       'disk_type': 'file',
                       'disk_format': 'qcow2',
                       'disk_source_protocol': 'file'})
        blk_source_image_after_converted = os.path.join(pre_set_root_dir, syslink_top_img)
        skip_first_one = True
        return replace_disk_image, blk_source_image_after_converted, skip_first_one, backing_chain_list

    def validate_blockcommit_after_libvirtd_restart():
        """Validate blockcommit after libvirtd restart"""
        logging.debug("phase three blockcommit .....")
        counts = 1
        phase_three_blockcommit_options = " --active"
        libvirt_disk.do_blockcommit_repeatedly(vm, 'vda', phase_three_blockcommit_options, counts)
        time.sleep(3)
        # Before restart libvirtd
        mirror_content_before_restart = libvirt_disk.get_mirror_part_in_xml(vm, disk_target)
        logging.debug(mirror_content_before_restart)
        utils_libvirtd.libvirtd_restart()
        # After restart libvirtd
        mirror_content_after_restart = libvirt_disk.get_mirror_part_in_xml(vm, disk_target)
        logging.debug(mirror_content_after_restart)
        # Check whether mirror content is identical with previous one
        if mirror_content_before_restart != mirror_content_after_restart:
            test.fail("The mirror part content changed after libvirtd restarted")
        virsh.blockjob(vm_name, disk_target, '--abort', ignore_status=True)

    def prepare_case_scenarios(snap_del_disks, base_file):
        """
        Prepare case scenarios

        :param snap_del_disks: snapshot list
        :param base_file: base file for snapshot
        """
        index = len(snap_del_disks) - 1
        option = "--top %s --base %s --delete --verbose --wait"
        scenarios = {}
        scenarios.update({"middle-to-middle": {'blkcomopt':
                          option % (snap_del_disks[index - 1], snap_del_disks[index - 2]),
                          'top': snap_del_disks[index - 1],
                          'base': snap_del_disks[index - 2]}})
        scenarios.update({"middle-to-base": {'blkcomopt':
                          option % (snap_del_disks[index - 1], base_file),
                          'top': snap_del_disks[index - 1],
                          'base': base_file}})
        scenarios.update({"top-to-middle": {'blkcomopt':
                          option % (snap_del_disks[index], snap_del_disks[index - 2]) + "  --active",
                          'top': snap_del_disks[index],
                          'base': snap_del_disks[index - 2]}})
        scenarios.update({"top-to-base": {'blkcomopt':
                                          "--top %s --delete --verbose --wait --active --pivot"
                                          % (snap_del_disks[index]),
                                          "top": snap_del_disks[index],
                                          "base": snap_del_disks[index]}})
        scenarios.update({"abort-top-job": {'blkcomopt':
                                            "--top %s --delete --verbose --wait --active --pivot --bandwidth 1"
                                            % (snap_del_disks[index]),
                                            "top": snap_del_disks[index],
                                            "base": snap_del_disks[index]}})
        return scenarios

    def loop_case_in_scenarios(scenarios):
        """
        Loop case scenarios

        :param scenarios: scenario list
        """
        # loop each scenario
        for case, opt in list(scenarios.items()):
            logging.debug("Begin scenario: %s testing....................", case)
            reverse = False
            if vm.is_alive():
                vm.destroy(gracefully=False)
            # Reset VM to initial state
            vmxml_backup.sync("--snapshots-metadata")
            vm.start()
            snap_del_disks = libvirt_disk.make_external_disk_snapshots(vm, disk_target, snapshot_prefix, snapshot_take)
            tmp_option = opt.get('blkcomopt')
            top_file = opt.get('top')
            base_file = opt.get('base')
            if 'abort' in case:
                fill_vm_with_contents()
                ignite_blockcommit_thread = threading.Thread(target=virsh.blockcommit,
                                                             args=(vm_name, disk_target, tmp_option,),
                                                             kwargs={'ignore_status': True, 'debug': True})
                ignite_blockcommit_thread.start()
                ignite_blockcommit_thread.join(2)
                virsh.blockjob(vm_name, disk_target, " --abort", ignore_status=False)
                reverse = True
            else:
                libvirt_disk.do_blockcommit_repeatedly(vm, 'vda', tmp_option, 1)
            # Need pivot to make effect
            if "--active" in tmp_option and "--pivot" not in tmp_option:
                virsh.blockjob(vm_name, disk_target, '--pivot', ignore_status=True)
            check_file_not_exists(pre_set_root_dir, top_file, reverse=reverse)
            if 'top' not in case:
                check_backing_chain_file_not_exists(snap_del_disks[len(snap_del_disks) - 1], top_file)
            libvirt_disk.cleanup_snapshots(vm, snap_del_disks)
            del snap_del_disks[:]

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vm_state = params.get("vm_state", "running")

    virsh_dargs = {'debug': True}
    status_error = ("yes" == params.get("status_error", "no"))
    restart_libvirtd = ("yes" == params.get("restart_libvirtd", "no"))
    validate_delete_option = ("yes" == params.get("validate_delete_option", "no"))

    tmp_dir = data_dir.get_data_dir()
    top_inactive = ("yes" == params.get("top_inactive"))
    base_option = params.get("base_option", "none")
    bandwidth = params.get("blockcommit_bandwidth", "")

    disk_target = params.get("disk_target", "vda")
    disk_format = params.get("disk_format", "qcow2")
    disk_type = params.get("disk_type")
    disk_src_protocol = params.get("disk_source_protocol")

    pool_name = params.get("pool_name")
    pool_target = params.get("pool_target")
    pool_type = params.get("pool_type")
    emulated_image = params.get("emulated_image")
    syslink_top_img = params.get("syslink_top_img")
    snapshot_take = int(params.get("snapshot_take", "4"))
    snapshot_prefix = params.get("snapshot_prefix", "snapshot")

    first_src_file = libvirt_disk.get_first_disk_source(vm)
    blk_source_image = os.path.basename(first_src_file)
    pre_set_root_dir = os.path.dirname(first_src_file)

    snapshot_external_disks = []
    skip_first_one = False
    snap_del_disks = []
    volume_path_list = []
    kkwargs = params.copy()
    pvt = libvirt.PoolVolumeTest(test, params)

    # A backup of original vm
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    # Abort the test if there are snapshots already
    exsiting_snaps = virsh.snapshot_list(vm_name)
    if len(exsiting_snaps) != 0:
        test.fail("There are snapshots created for %s already" %
                  vm_name)
    try:
        if vm.is_alive():
            vm.destroy(gracefully=False)
        if disk_src_protocol == 'iscsi':
            replace_disk_image, blk_source_image_after_converted, backing_chain_list = setup_iscsi_env()
        if disk_src_protocol == "rbd":
            replace_disk_image, blk_source_image_after_converted, backing_chain_list = setup_rbd_env()
        if disk_src_protocol == "pool":
            replace_disk_image, blk_source_image_after_converted, skip_first_one, backing_chain_list = setup_volume_pool_env()
        libvirt.set_vm_disk(vm, params, tmp_dir)

        # get a vm session before snapshot
        session = vm.wait_for_login()
        old_parts = utils_disk.get_parts_list(session)
        # Check backing files
        check_chain_backing_files(replace_disk_image, backing_chain_list)

        if vm_state == "paused":
            vm.pause()
        # Do phase one blockcommit
        phase_one_blockcommit_options = "--active --verbose --shallow --pivot --keep-relative"
        counts = len(backing_chain_list)
        if bandwidth and base_option == "base":
            phase_one_blockcommit_options = "--top vda[1] --base vda[3] --keep-relative --bandwidth %s --active" % bandwidth
        if restart_libvirtd:
            utils_libvirtd.libvirtd_restart()
        if base_option == "shallow":
            libvirt_disk.do_blockcommit_repeatedly(vm, 'vda', phase_one_blockcommit_options, counts)
        elif base_option == "base":
            counts = 1
            libvirt_disk.do_blockcommit_repeatedly(vm, 'vda', phase_one_blockcommit_options, counts)
            check_blockcommit_with_bandwidth(backing_chain_list[::-1])
            virsh.blockjob(vm_name, disk_target, '--abort', ignore_status=True)
            # Pivot commits to bottom one of backing chain
            phase_one_blockcommit_options = "--active --verbose --shallow --pivot --keep-relative"
            counts = len(backing_chain_list)
            libvirt_disk.do_blockcommit_repeatedly(vm, 'vda', phase_one_blockcommit_options, counts)
        #Check top image after phase one block commit
        check_top_image_in_xml(blk_source_image_after_converted)

        # Do snapshots
        _, snapshot_external_disks = libvirt_disk.create_reuse_external_snapshots(
            vm, pre_set_root_dir, skip_first_one, disk_target)
        # Set blockcommit_options
        phase_two_blockcommit_options = "--verbose --keep-relative --shallow --active --pivot"

        # Run phase two blockcommit with snapshots
        counts = len(snapshot_external_disks) - 1
        libvirt_disk.do_blockcommit_repeatedly(vm, 'vda', phase_two_blockcommit_options, counts)
        #Check top image after phase two block commit
        check_top_image_in_xml(snapshot_external_disks)
        # Run dependent restart_libvirtd case
        if restart_libvirtd:
            validate_blockcommit_after_libvirtd_restart()
        # Run dependent validate_delete_option case
        if validate_delete_option:
            # Run blockcommit with snapshots to validate delete option
            # Test scenarios can be referred from https://bugzilla.redhat.com/show_bug.cgi?id=1008350
            logging.debug("Blockcommit with delete option .....")
            base_file = first_src_file
            # Get first attempt snapshot lists
            if vm.is_alive():
                vm.destroy(gracefully=False)
                # Reset VM to initial state
                vmxml_backup.sync("--snapshots-metadata")
                vm.start()
            snap_del_disks = libvirt_disk.make_external_disk_snapshots(vm, disk_target, snapshot_prefix, snapshot_take)
            scenarios = prepare_case_scenarios(snap_del_disks, base_file)
            libvirt_disk.cleanup_snapshots(vm, snap_del_disks)
            del snap_del_disks[:]
            loop_case_in_scenarios(scenarios)
    finally:
        if vm.is_alive():
            vm.destroy(gracefully=False)
        # Recover xml of vm.
        vmxml_backup.sync("--snapshots-metadata")

        # Delete reuse external disk if exists
        for disk in snapshot_external_disks:
            if os.path.exists(disk):
                os.remove(disk)
        # Delete snapshot disk
        libvirt_disk.cleanup_snapshots(vm, snap_del_disks)
        # Clean up created folders
        for folder in [chr(letter) for letter in range(ord('a'), ord('a') + 4)]:
            rm_cmd = "rm -rf %s" % os.path.join(pre_set_root_dir, folder)
            process.run(rm_cmd, shell=True)

        # Remove ceph config file if created
        if disk_src_protocol == "rbd":
            libvirt_ceph_utils.create_or_cleanup_ceph_backend_vm_disk(vm, params, is_setup=False)
        elif disk_src_protocol == 'iscsi' or 'iscsi_target' in locals():
            libvirt.setup_or_cleanup_iscsi(is_setup=False)
        elif disk_src_protocol == 'pool':
            pvt.cleanup_pool(pool_name, pool_type, pool_target, emulated_image)
            rm_cmd = "rm -rf %s" % pool_target
            process.run(rm_cmd, shell=True)

        # Recover images xattr if having some
        dirty_images = libvirt_disk.get_images_with_xattr(vm)
        if dirty_images:
            libvirt_disk.clean_images_with_xattr(dirty_images)
            test.error("VM's image(s) having xattr left")
