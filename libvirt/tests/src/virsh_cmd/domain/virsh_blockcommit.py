import os
import logging
import tempfile
import collections

import aexpect

from avocado.utils import process

from multiprocessing.pool import ThreadPool

from virttest import virsh
from virttest import data_dir
from virttest import utils_libvirtd
from virttest import utils_misc
from virttest import utils_package
from virttest import libvirt_storage
from virttest import ceph
from virttest import gluster
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt

from virttest import libvirt_version


def get_images_with_xattr(vm):
    """
    Get the image path(s) which still having xattr left of a vm

    :param vm: The vm to be checked
    :return: The image path list which having xattr left
    """
    disks = vm.get_disk_devices()
    dirty_images = []
    check_cmd = "getfattr -m trusted.libvirt.security -d %s"
    for disk in disks:
        disk_path = disks[disk]['source']
        getfattr_result = process.run(check_cmd % disk_path, shell=True)
        if "selinux" in getfattr_result.stdout_text:
            dirty_images.append(disk_path)
            logging.debug("Image '%s' having xattr left: %s",
                          disk_path, getfattr_result.stdout)
    return dirty_images


def clean_images_with_xattr(dirty_images):
    """
    Clean the images' xattr

    :param dirty_images: The image list to be cleaned
    """
    clean_cmd = ("for i in $(getfattr -m trusted.libvirt.security -d %s |"
                 "grep 'trusted.libvirt'| cut -f 1 -d '=');"
                 "do setfattr -x $i %s;done")
    for image in dirty_images:
        cmd = clean_cmd % (image, image)
        process.run(cmd, shell=True)


def check_chain_xml(disk_xml, chain_lst):
    """
    param disk_xml: disk xmltreefile
    param chain_lst: list, expected backing chain list
    return: True or False
    """
    logging.debug("expected backing chain list is %s", chain_lst)
    src_file = disk_xml.find('source').get('file')
    if src_file != chain_lst[0]:
        logging.error("Current top img %s is not expected", src_file)
        return False
    for i in range(1, len(chain_lst)):
        backing_xml = disk_xml.find('backingStore')
        src_element = backing_xml.find('source')
        src_file = None
        for elem in ('file', 'name', 'dev'):
            elem_val = src_element.get(elem)
            if elem_val:
                src_file = elem_val
                break
        if src_file != chain_lst[i]:
            logging.error("backing store chain file %s is "
                          "not expected" % src_file)
            return False
        disk_xml = disk_xml.reroot('backingStore')
        logging.debug("after reroot the xml is %s", disk_xml)

    return True


def check_bandwidth_thread(func, vm_name, blk_target, bandwidth, test):
    """
    Create one separate thread to check bandwidth

    :param func: function name
    :param vm_name: string, VM name
    :param blk_target: string, target disk
    :param bandwidth: string, bandwidth value
    :param test: string, test case object
    """
    ret = utils_misc.wait_for(lambda: func(vm_name, blk_target, "bandwidth", bandwidth), 30)
    if not ret:
        test.fail("Failed to get bandwidth limit output")


def run(test, params, env):
    """
    Test command: virsh blockcommit <domain> <path>

    1) Prepare test environment.
    2) Commit changes from a snapshot down to its backing image.
    3) Recover test environment.
    4) Check result.
    """

    def make_disk_snapshot(postfix_n, snapshot_take, is_check_snapshot_tree=False, is_create_image_file_in_vm=False):
        """
        Make external snapshots for disks only.

        :param postfix_n: postfix option
        :param snapshot_take: snapshots taken.
        :param is_create_image_file_in_vm: create image file in VM.
        """
        # Add all disks into command line.
        disks = vm.get_disk_devices()

        # Make three external snapshots for disks only
        for count in range(1, snapshot_take):
            options = "%s_%s %s%s-desc " % (postfix_n, count,
                                            postfix_n, count)
            options += "--disk-only --atomic --no-metadata"
            if needs_agent:
                options += " --quiesce"

            for disk in disks:
                disk_detail = disks[disk]
                basename = os.path.basename(disk_detail['source'])

                # Remove the original suffix if any, appending
                # ".postfix_n[0-9]"
                diskname = basename.split(".")[0]
                snap_name = "%s.%s%s" % (diskname, postfix_n, count)
                disk_external = os.path.join(tmp_dir, snap_name)

                snapshot_external_disks.append(disk_external)
                options += " %s,snapshot=external,file=%s" % (disk,
                                                              disk_external)

            if is_check_snapshot_tree:
                options = options.replace("--no-metadata", "")
            cmd_result = virsh.snapshot_create_as(vm_name, options,
                                                  ignore_status=True,
                                                  debug=True)
            status = cmd_result.exit_status
            if status != 0:
                test.fail("Failed to make snapshots for disks!")

            if is_create_image_file_in_vm:
                create_file_cmd = "dd if=/dev/urandom of=/mnt/snapshot_%s.img bs=1M count=2" % count
                session.cmd_status_output(create_file_cmd)
                created_image_files_in_vm.append("snapshot_%s.img" % count)

            # Create a file flag in VM after each snapshot
            flag_file = tempfile.NamedTemporaryFile(prefix=("snapshot_test_"),
                                                    dir="/tmp")
            file_path = flag_file.name
            flag_file.close()

            status, output = session.cmd_status_output("touch %s" % file_path)
            if status:
                test.fail("Touch file in vm failed. %s" % output)
            snapshot_flag_files.append(file_path)

        def check_snapshot_tree():
            """
            Check whether predefined snapshot names are equals to
            snapshot names by virsh snapshot-list --tree
            """
            predefined_snapshot_name_list = []
            for count in range(1, snapshot_take):
                predefined_snapshot_name_list.append("%s_%s" % (postfix_n, count))
            snapshot_list_cmd = "virsh snapshot-list %s --tree" % vm_name
            result_output = process.run(snapshot_list_cmd,
                                        ignore_status=True, shell=True).stdout_text
            virsh_snapshot_name_list = []
            for line in result_output.rsplit("\n"):
                strip_line = line.strip()
                if strip_line and "|" not in strip_line:
                    virsh_snapshot_name_list.append(strip_line)
            # Compare two lists in their order and values, all need to be same.
            compare_list = [out_p for out_p, out_v in zip(predefined_snapshot_name_list, virsh_snapshot_name_list)
                            if out_p not in out_v]
            if compare_list:
                test.fail("snapshot tree not correctly returned.")

        # If check_snapshot_tree is True, check snapshot tree output.
        if is_check_snapshot_tree:
            check_snapshot_tree()

    def get_first_disk_source():
        """
        Get disk source of first device
        :return: first disk of first device.
        """
        first_device = vm.get_first_disk_devices()
        first_disk_src = first_device['source']
        return first_disk_src

    def make_relative_path_backing_files(pre_set_root_dir=None):
        """
        Create backing chain files of relative path.
        :param pre_set_root_dir: preset root dir
        :return: absolute path of top active file
        """
        first_disk_source = get_first_disk_source()
        basename = os.path.basename(first_disk_source)
        if pre_set_root_dir is None:
            root_dir = os.path.dirname(first_disk_source)
        else:
            root_dir = pre_set_root_dir
        cmd = "mkdir -p %s" % os.path.join(root_dir, '{b..d}')
        ret = process.run(cmd, shell=True)
        libvirt.check_exit_status(ret)

        # Make three external relative path backing files.
        backing_file_dict = collections.OrderedDict()
        backing_file_dict["b"] = "../%s" % basename
        backing_file_dict["c"] = "../b/b.img"
        backing_file_dict["d"] = "../c/c.img"
        if pre_set_root_dir:
            backing_file_dict["b"] = "%s" % first_disk_source
            backing_file_dict["c"] = "%s/b/b.img" % root_dir
            backing_file_dict["d"] = "%s/c/c.img" % root_dir
        disk_format = params.get("disk_format", "qcow2")
        for key, value in list(backing_file_dict.items()):
            backing_file_path = os.path.join(root_dir, key)
            cmd = ("cd %s && qemu-img create -f %s -o backing_file=%s,backing_fmt=%s %s.img"
                   % (backing_file_path, "qcow2", value, disk_format, key))
            ret = process.run(cmd, shell=True)
            disk_format = "qcow2"
            libvirt.check_exit_status(ret)
        return os.path.join(backing_file_path, "d.img")

    def check_chain_backing_files(disk_src_file, expect_backing_file=False):
        """
        Check backing chain files of relative path after blockcommit.

        :param disk_src_file: first disk src file.
        :param expect_backing_file: whether it expect to have backing files.
        """
        first_disk_source = get_first_disk_source()
        # Validate source image need refer to original one after active blockcommit
        if not expect_backing_file and disk_src_file not in first_disk_source:
            test.fail("The disk image path:%s doesn't include the origin image: %s" % (first_disk_source, disk_src_file))
        # Validate source image doesn't have backing files after active blockcommit
        cmd = "qemu-img info %s --backing-chain" % first_disk_source
        if qemu_img_locking_feature_support:
            cmd = "qemu-img info -U %s --backing-chain" % first_disk_source
        ret = process.run(cmd, shell=True).stdout_text.strip()
        if expect_backing_file:
            if 'backing file' not in ret:
                test.fail("The disk image doesn't have backing files")
            else:
                logging.debug("The actual qemu-img output:%s\n", ret)
        else:
            if 'backing file' in ret:
                test.fail("The disk image still have backing files")
            else:
                logging.debug("The actual qemu-img output:%s\n", ret)

    def create_reuse_external_snapshots(pre_set_root_dir=None):
        """
        Create reuse external snapshots
        :param pre_set_root_dir: preset root directory
        :return: absolute path of base file
        """
        if pre_set_root_dir is None:
            first_disk_source = get_first_disk_source()
            basename = os.path.basename(first_disk_source)
            root_dir = os.path.dirname(first_disk_source)
        else:
            root_dir = pre_set_root_dir
        meta_options = " --reuse-external --disk-only --no-metadata"
        # Make three external relative path backing files.
        backing_file_dict = collections.OrderedDict()
        backing_file_dict["b"] = "b.img"
        backing_file_dict["c"] = "c.img"
        backing_file_dict["d"] = "d.img"
        for key, value in list(backing_file_dict.items()):
            backing_file_path = os.path.join(root_dir, key)
            external_snap_shot = "%s/%s" % (backing_file_path, value)
            snapshot_external_disks.append(external_snap_shot)
            options = "%s --diskspec %s,file=%s" % (meta_options, disk_target, external_snap_shot)
            cmd_result = virsh.snapshot_create_as(vm_name, options,
                                                  ignore_status=False,
                                                  debug=True)
            libvirt.check_exit_status(cmd_result)
        logging.debug('reuse external snapshots:%s' % snapshot_external_disks)
        return root_dir

    def check_file_in_vm():
        """
        Check whether certain image files exists in VM internal.
        """
        for img_file in created_image_files_in_vm:
            status, output = session.cmd_status_output("ls -l /mnt/%s" % img_file)
            logging.debug(output)
            if status:
                test.fail("blockcommit from top to base failed when ls image file in VM: %s" % output)

    def do_blockcommit_pivot_repeatedly():
        """
        Validate bugzilla:https://bugzilla.redhat.com/show_bug.cgi?id=1857735
        """
        # Make external snapshot,pivot and delete snapshot file repeatedly.
        tmp_snapshot_name = "external_snapshot_" + "repeated.qcow2"
        block_target = 'vda'
        for count in range(0, 5):
            options = "%s " % tmp_snapshot_name
            options += "--disk-only --atomic"
            disk_external = os.path.join(tmp_dir, tmp_snapshot_name)
            options += "  --diskspec  %s,snapshot=external,file=%s" % (block_target, disk_external)
            virsh.snapshot_create_as(vm_name, options,
                                     ignore_status=False, debug=True)
            virsh.blockcommit(vm_name, block_target,
                              " --active --pivot ", ignore_status=False, debug=True)
            virsh.snapshot_delete(vm_name, tmp_snapshot_name, " --metadata")
            libvirt.delete_local_disk('file', disk_external)

    # MAIN TEST CODE ###
    # Process cartesian parameters
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    snapshot_take = int(params.get("snapshot_take", '0'))
    vm_state = params.get("vm_state", "running")
    needs_agent = "yes" == params.get("needs_agent", "yes")
    replace_vm_disk = "yes" == params.get("replace_vm_disk", "no")
    top_inactive = ("yes" == params.get("top_inactive"))
    with_timeout = ("yes" == params.get("with_timeout_option", "no"))
    cmd_timeout = params.get("cmd_timeout", "1")
    status_error = ("yes" == params.get("status_error", "no"))
    base_option = params.get("base_option", "none")
    middle_base = "yes" == params.get("middle_base", "no")
    pivot_opt = "yes" == params.get("pivot_opt", "no")
    snap_in_mirror = "yes" == params.get("snap_in_mirror", "no")
    snap_in_mirror_err = "yes" == params.get("snap_in_mirror_err", "no")
    with_active_commit = "yes" == params.get("with_active_commit", "no")
    multiple_chain = "yes" == params.get("multiple_chain", "no")
    virsh_dargs = {'debug': True}
    check_snapshot_tree = "yes" == params.get("check_snapshot_tree", "no")
    bandwidth = params.get("blockcommit_bandwidth", "")
    bandwidth_byte = "yes" == params.get("bandwidth_byte", "no")
    disk_target = params.get("disk_target", "vda")
    disk_format = params.get("disk_format", "qcow2")
    reuse_external_snapshot = "yes" == params.get("reuse_external_snapshot", "no")
    restart_vm_before_commit = "yes" == params.get("restart_vm_before_commit", "no")
    check_image_file_in_vm = "yes" == params.get("check_image_file_in_vm", "no")
    pre_set_root_dir = None
    blk_source_folder = None
    convert_qcow2_image_to_raw = "yes" == params.get("convert_qcow2_image_to_raw", "no")
    repeatedly_do_blockcommit_pivot = "yes" == params.get("repeatedly_do_blockcommit_pivot", "no")
    from_top_without_active_option = "yes" == params.get("from_top_without_active_option", "no")
    top_to_middle_keep_overlay = "yes" == params.get("top_to_middle_keep_overlay", "no")
    block_disk_type_based_on_file_backing_file = "yes" == params.get("block_disk_type_based_on_file_backing_file", "no")
    block_disk_type_based_on_gluster_backing_file = "yes" == params.get("block_disk_type_based_on_gluster_backing_file", "no")

    # Check whether qemu-img need add -U suboption since locking feature was added afterwards qemu-2.10
    qemu_img_locking_feature_support = libvirt_storage.check_qemu_image_lock_support()
    backing_file_relative_path = "yes" == params.get("backing_file_relative_path", "no")

    # Process domain disk device parameters
    disk_type = params.get("disk_type")
    disk_src_protocol = params.get("disk_source_protocol")
    restart_tgtd = params.get("restart_tgtd", 'no')
    vol_name = params.get("vol_name")
    tmp_dir = data_dir.get_data_dir()
    pool_name = params.get("pool_name", "gluster-pool")
    brick_path = os.path.join(tmp_dir, pool_name)

    if not top_inactive:
        if not libvirt_version.version_compare(1, 2, 4):
            test.cancel("live active block commit is not supported"
                        " in current libvirt version.")

    # A backup of original vm
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    # Abort the test if there are snapshots already
    exsiting_snaps = virsh.snapshot_list(vm_name)
    if len(exsiting_snaps) != 0:
        test.fail("There are snapshots created for %s already" %
                  vm_name)

    snapshot_external_disks = []
    cmd_session = None
    # Prepare a blank params to confirm if delete the configure at the end of the test
    ceph_cfg = ''
    try:
        if disk_src_protocol == 'iscsi' and disk_type == 'block' and reuse_external_snapshot:
            first_disk = vm.get_first_disk_devices()
            pre_set_root_dir = os.path.dirname(first_disk['source'])

        if disk_src_protocol == 'iscsi' and disk_type == 'network':
            if not libvirt_version.version_compare(1, 0, 4):
                test.cancel("'iscsi' disk doesn't support in"
                            " current libvirt version.")

        # Set vm xml and guest agent
        if replace_vm_disk:
            if disk_src_protocol == "rbd" and disk_type == "network":
                src_host = params.get("disk_source_host", "EXAMPLE_HOSTS")
                mon_host = params.get("mon_host", "EXAMPLE_MON_HOST")
                # Create config file if it doesn't exist
                ceph_cfg = ceph.create_config_file(mon_host)
                if src_host.count("EXAMPLE") or mon_host.count("EXAMPLE"):
                    test.cancel("Please provide rbd host first.")

                params.update(
                   {"disk_source_name": os.path.join(
                      pool_name, 'rbd_'+utils_misc.generate_random_string(4)+'.img')})
                if utils_package.package_install(["ceph-common"]):
                    ceph.rbd_image_rm(mon_host, *params.get("disk_source_name").split('/'))
                else:
                    test.error('Failed to install ceph-common to clean image.')
            if backing_file_relative_path:
                if vm.is_alive():
                    vm.destroy(gracefully=False)
                first_src_file = get_first_disk_source()
                blk_source_image = os.path.basename(first_src_file)
                blk_source_folder = os.path.dirname(first_src_file)
                replace_disk_image = make_relative_path_backing_files()
                params.update({'disk_source_name': replace_disk_image,
                               'disk_type': 'file',
                               'disk_src_protocol': 'file'})
                vm.start()
            if convert_qcow2_image_to_raw:
                if vm.is_alive():
                    vm.destroy(gracefully=False)
                first_src_file = get_first_disk_source()
                blk_source_image = os.path.basename(first_src_file)
                blk_source_folder = os.path.dirname(first_src_file)
                blk_source_image_after_converted = "%s/converted_%s" % (blk_source_folder, blk_source_image)
                # Convert the image from qcow2 to raw
                convert_disk_cmd = ("qemu-img convert"
                                    " -O %s %s %s" % (disk_format, first_src_file, blk_source_image_after_converted))
                process.run(convert_disk_cmd, ignore_status=False, shell=True)
                params.update({'disk_source_name': blk_source_image_after_converted,
                               'disk_type': 'file',
                               'disk_src_protocol': 'file'})
            libvirt.set_vm_disk(vm, params, tmp_dir)

        if needs_agent:
            vm.prepare_guest_agent()

        if repeatedly_do_blockcommit_pivot:
            do_blockcommit_pivot_repeatedly()

        # Create block type disk on file backing file
        if block_disk_type_based_on_file_backing_file or block_disk_type_based_on_gluster_backing_file:
            if not vm.is_alive():
                vm.start()
            first_src_file = get_first_disk_source()
            libvirt.setup_or_cleanup_iscsi(is_setup=False)
            iscsi_target = libvirt.setup_or_cleanup_iscsi(is_setup=True)
            block_type_backstore = iscsi_target
            if block_disk_type_based_on_file_backing_file:
                first_src_file = get_first_disk_source()
            if block_disk_type_based_on_gluster_backing_file:
                first_src_file = "gluster://%s/%s/gluster.qcow2" % (params.get("gluster_server_ip"), params.get("vol_name"))
            backing_file_create_cmd = ("qemu-img create -f %s -o backing_file=%s,backing_fmt=%s %s"
                                       % ("qcow2", first_src_file, "qcow2", block_type_backstore))
            process.run(backing_file_create_cmd, ignore_status=False, shell=True)
            meta_options = " --reuse-external --disk-only --no-metadata"
            options = "%s --diskspec %s,file=%s,stype=block" % (meta_options, 'vda', block_type_backstore)
            virsh.snapshot_create_as(vm_name, options,
                                     ignore_status=False,
                                     debug=True)

        # The first disk is supposed to include OS
        # We will perform blockcommit operation for it.
        first_disk = vm.get_first_disk_devices()
        blk_source = first_disk['source']
        blk_target = first_disk['target']
        snapshot_flag_files = []
        created_image_files_in_vm = []

        # get a vm session before snapshot
        session = vm.wait_for_login()
        # do snapshot
        postfix_n = 'snap'
        if reuse_external_snapshot:
            make_relative_path_backing_files(pre_set_root_dir)
            blk_source_folder = create_reuse_external_snapshots(pre_set_root_dir)
        else:
            make_disk_snapshot(postfix_n, snapshot_take, check_snapshot_tree, check_image_file_in_vm)

        basename = os.path.basename(blk_source)
        diskname = basename.split(".")[0]
        snap_src_lst = [blk_source]
        if multiple_chain:
            snap_name = "%s.%s1" % (diskname, postfix_n)
            snap_top = os.path.join(tmp_dir, snap_name)
            top_index = snapshot_external_disks.index(snap_top) + 1
            omit_list = snapshot_external_disks[top_index:]
            vm.destroy(gracefully=False)
            vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
            disk_xml = ''
            disk_xmls = vmxml.get_devices(device_type="disk")
            for disk in disk_xmls:
                if disk.get('device_tag') == 'disk':
                    disk_xml = disk
                    break

            vmxml.del_device(disk_xml)
            disk_dict = {'attrs': {'file': snap_top}}
            disk_xml.source = disk_xml.new_disk_source(**disk_dict)
            if libvirt_version.version_compare(6, 0, 0):
                bs_source = {'file': blk_source}
                bs_dict = {"type": params.get("disk_type", "file"),
                           "format": {'type': params.get("disk_format", "qcow2")}}
                new_bs = disk_xml.new_backingstore(**bs_dict)
                new_bs["source"] = disk_xml.backingstore.new_source(**bs_source)
                disk_xml.backingstore = new_bs
            vmxml.add_device(disk_xml)
            vmxml.sync()
            vm.start()
            session = vm.wait_for_login()
            postfix_n = 'new_snap'
            make_disk_snapshot(postfix_n, snapshot_take)
            snap_src_lst = [blk_source]
            snap_src_lst += snapshot_external_disks
            logging.debug("omit list is %s", omit_list)
            for i in omit_list:
                snap_src_lst.remove(i)
        else:
            # snapshot src file list
            snap_src_lst += snapshot_external_disks
        backing_chain = ''
        for i in reversed(list(range(snapshot_take))):
            if i == 0:
                backing_chain += "%s" % snap_src_lst[i]
            else:
                backing_chain += "%s -> " % snap_src_lst[i]

        logging.debug("The backing chain is: %s" % backing_chain)

        # check snapshot disk xml backingStore is expected
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        disks = vmxml.devices.by_device_tag('disk')
        disk_xml = None
        for disk in disks:
            if disk.target['dev'] != blk_target:
                continue
            else:
                if disk.device != 'disk':
                    continue
                disk_xml = disk.xmltreefile
                logging.debug("the target disk xml after snapshot is %s",
                              disk_xml)
                break

        if not disk_xml:
            test.fail("Can't find disk xml with target %s" %
                      blk_target)
        elif libvirt_version.version_compare(1, 2, 4):
            # backingStore element introduced in 1.2.4
            chain_lst = snap_src_lst[::-1]
            ret = check_chain_xml(disk_xml, chain_lst)
            if not ret:
                test.fail("Domain image backing chain check failed")

        # set blockcommit_options
        top_image = None
        blockcommit_options = "--wait --verbose"

        if with_timeout:
            blockcommit_options += " --timeout %s" % cmd_timeout

        if base_option == "shallow":
            blockcommit_options += " --shallow"
        elif base_option == "base":
            if middle_base:
                snap_name = "%s.%s1" % (diskname, postfix_n)
                blk_source = os.path.join(tmp_dir, snap_name)
            blockcommit_options += " --base %s" % blk_source
        if len(bandwidth):
            blockcommit_options += " --bandwidth %s" % bandwidth
        if bandwidth_byte:
            blockcommit_options += " --bytes"
        if top_inactive:
            snap_name = "%s.%s2" % (diskname, postfix_n)
            top_image = os.path.join(tmp_dir, snap_name)
            if reuse_external_snapshot:
                index = len(snapshot_external_disks) - 2
                top_image = snapshot_external_disks[index]
            blockcommit_options += " --top %s" % top_image
        else:
            blockcommit_options += " --active"
            if pivot_opt:
                blockcommit_options += " --pivot"

        if from_top_without_active_option:
            blockcommit_options = blockcommit_options.replace("--active", "")

        if top_to_middle_keep_overlay:
            blockcommit_options = blockcommit_options.replace("--active", "")
            blockcommit_options = blockcommit_options.replace("--pivot", "")
            blockcommit_options += " --keep-overlay"

        if restart_vm_before_commit:
            top = 2
            base = len(snapshot_external_disks)
            blockcommit_options = ("--top %s[%d] --base %s[%d] --verbose --wait --keep-relative"
                                   % (disk_target, top, disk_target, base))
            vm.destroy(gracefully=True)
            vm.start()

        if vm_state == "shut off":
            vm.destroy(gracefully=True)

        if with_active_commit:
            # inactive commit follow active commit will fail with bug 1135339
            cmd = "virsh blockcommit %s %s --active --pivot" % (vm_name,
                                                                blk_target)
            cmd_session = aexpect.ShellSession(cmd)

        if backing_file_relative_path:
            blockcommit_options = "  --active --verbose --shallow --pivot --keep-relative"
            block_commit_index = snapshot_take
            expect_backing_file = False
            # Do block commit using --active
            for count in range(1, snapshot_take):
                res = virsh.blockcommit(vm_name, blk_target,
                                        blockcommit_options, **virsh_dargs)
                libvirt.check_exit_status(res, status_error)
            if top_inactive:
                vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
                disk_xml = ''
                disk_xmls = vmxml.get_devices(device_type="disk")
                for disk in disk_xmls:
                    if disk.get('device_tag') == 'disk':
                        disk_xml = disk
                        break

                top_index = 1
                try:
                    top_index = disk_xml.backingstore.index
                except AttributeError:
                    pass
                else:
                    top_index = int(top_index)

                block_commit_index = snapshot_take - 1
                expect_backing_file = True
            for count in range(1, block_commit_index):
                # Do block commit with --wait if top_inactive
                if top_inactive:
                    blockcommit_options = ("  --wait --verbose --top vda[%d] "
                                           "--base vda[%d] --keep-relative"
                                           % (top_index, top_index+1))
                    if not libvirt_version.version_compare(6, 0, 0):
                        top_index = 1
                    else:
                        top_index += 1
                res = virsh.blockcommit(vm_name, blk_target,
                                        blockcommit_options, **virsh_dargs)
                libvirt.check_exit_status(res, status_error)

            check_chain_backing_files(blk_source_image, expect_backing_file)
            return

        if reuse_external_snapshot and not top_inactive:
            block_commit_index = len(snapshot_external_disks) - 1
            for index in range(block_commit_index):
                # Do block commit with --shallow --wait
                external_blockcommit_options = ("  --shallow --wait --verbose --top %s "
                                                % (snapshot_external_disks[index]))

                res = virsh.blockcommit(vm_name, blk_target,
                                        external_blockcommit_options, **virsh_dargs)
                libvirt.check_exit_status(res, status_error)
            # Do blockcommit with top active
            result = virsh.blockcommit(vm_name, blk_target,
                                       blockcommit_options, **virsh_dargs)
            # Check status_error
            libvirt.check_exit_status(result, status_error)
            return

        # Start one thread to check the bandwidth in output
        if bandwidth and bandwidth_byte:
            bandwidth += 'B'
            pool = ThreadPool(processes=1)
            pool.apply_async(check_bandwidth_thread, (libvirt.check_blockjob, vm_name, blk_target, bandwidth, test))

        # Run test case
        # Active commit does not support on rbd based disk with bug 1200726
        result = virsh.blockcommit(vm_name, blk_target,
                                   blockcommit_options, **virsh_dargs)
        # Check status_error
        libvirt.check_exit_status(result, status_error)

        # Skip check chain file as per test case description
        if restart_vm_before_commit:
            return

        if check_image_file_in_vm:
            check_file_in_vm()

        if result.exit_status and status_error:
            return

        while True:
            vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)

            disks = vmxml.devices.by_device_tag('disk')
            for disk in disks:
                if disk.target['dev'] != blk_target:
                    continue
                else:
                    disk_xml = disk.xmltreefile
                    break

            if not top_inactive:
                disk_mirror = disk_xml.find('mirror')
                if '--pivot' not in blockcommit_options:
                    if disk_mirror is not None:
                        job_type = disk_mirror.get('job')
                        job_ready = disk_mirror.get('ready')
                        src_element = disk_mirror.find('source')
                        disk_src_file = None
                        for elem in ('file', 'name', 'dev'):
                            elem_val = src_element.get(elem)
                            if elem_val:
                                disk_src_file = elem_val
                                break
                        err_msg = "blockcommit base source "
                        err_msg += "%s not expected" % disk_src_file
                        if '--shallow' in blockcommit_options:
                            if not multiple_chain:
                                if disk_src_file != snap_src_lst[2]:
                                    test.fail(err_msg)
                            else:
                                if disk_src_file != snap_src_lst[3]:
                                    test.fail(err_msg)
                        else:
                            if disk_src_file != blk_source:
                                test.fail(err_msg)
                        if libvirt_version.version_compare(1, 2, 7):
                            # The job attribute mentions which API started the
                            # operation since 1.2.7.
                            if job_type != 'active-commit':
                                test.fail("blockcommit job type '%s'"
                                          " not expected" % job_type)
                            if job_ready != 'yes':
                                # The attribute ready, if present, tracks
                                # progress of the job: yes if the disk is known
                                # to be ready to pivot, or, since 1.2.7, abort
                                # or pivot if the job is in the process of
                                # completing.
                                continue
                            else:
                                logging.debug("after active block commit job "
                                              "ready for pivot, the target disk"
                                              " xml is %s", disk_xml)
                                break
                        else:
                            break
                    else:
                        break
                else:
                    if disk_mirror is None:
                        logging.debug(disk_xml)
                        if "--shallow" in blockcommit_options:
                            chain_lst = snap_src_lst[::-1]
                            chain_lst.pop(0)
                            ret = check_chain_xml(disk_xml, chain_lst)
                            if not ret:
                                test.fail("Domain image backing "
                                          "chain check failed")
                            cmd_result = virsh.blockjob(vm_name, blk_target, '',
                                                        ignore_status=True, debug=True)
                            libvirt.check_exit_status(cmd_result)
                        elif "--base" in blockcommit_options:
                            chain_lst = snap_src_lst[::-1]
                            base_index = chain_lst.index(blk_source)
                            chain_lst = chain_lst[base_index:]
                            ret = check_chain_xml(disk_xml, chain_lst)
                            if not ret:
                                test.fail("Domain image backing "
                                          "chain check failed")
                        break
                    else:
                        # wait pivot after commit is synced
                        continue
            else:
                logging.debug("after inactive commit the disk xml is: %s"
                              % disk_xml)
                if libvirt_version.version_compare(1, 2, 4):
                    if "--shallow" in blockcommit_options:
                        chain_lst = snap_src_lst[::-1]
                        chain_lst.remove(top_image)
                        ret = check_chain_xml(disk_xml, chain_lst)
                        if not ret:
                            test.fail("Domain image backing chain "
                                      "check failed")
                    elif "--base" in blockcommit_options:
                        chain_lst = snap_src_lst[::-1]
                        top_index = chain_lst.index(top_image)
                        base_index = chain_lst.index(blk_source)
                        val_tmp = []
                        for i in range(top_index, base_index):
                            val_tmp.append(chain_lst[i])
                        for i in val_tmp:
                            chain_lst.remove(i)
                        ret = check_chain_xml(disk_xml, chain_lst)
                        if not ret:
                            test.fail("Domain image backing chain "
                                      "check failed")
                    break
                else:
                    break

        # Check flag files
        if not vm_state == "shut off" and not multiple_chain:
            for flag in snapshot_flag_files:
                status, output = session.cmd_status_output("cat %s" % flag)
                if status:
                    test.fail("blockcommit failed: %s" % output)

        if not pivot_opt and snap_in_mirror:
            # do snapshot during mirror phase
            snap_path = "%s/%s.snap" % (tmp_dir, vm_name)
            snap_opt = "--disk-only --atomic --no-metadata "
            snap_opt += "vda,snapshot=external,file=%s" % snap_path
            snapshot_external_disks.append(snap_path)
            cmd_result = virsh.snapshot_create_as(vm_name, snap_opt,
                                                  ignore_statues=True,
                                                  debug=True)
            libvirt.check_exit_status(cmd_result, snap_in_mirror_err)
    finally:
        if vm.is_alive():
            vm.destroy(gracefully=False)
        # Clean ceph image if used in test
        if 'mon_host' in locals():
            if utils_package.package_install(["ceph-common"]):
                disk_source_name = params.get("disk_source_name")
                cmd = ("rbd -m {0} info {1} && rbd -m {0} rm "
                       "{1}".format(mon_host, disk_source_name))
                cmd_result = process.run(cmd, ignore_status=True, shell=True)
                logging.debug("result of rbd removal: %s", cmd_result)
            else:
                logging.debug('Failed to install ceph-common to clean ceph.')
        # Recover xml of vm.
        vmxml_backup.sync("--snapshots-metadata")

        # Remove ceph configure file if created
        if ceph_cfg:
            os.remove(ceph_cfg)

        if cmd_session:
            cmd_session.close()
        for disk in snapshot_external_disks:
            if os.path.exists(disk):
                os.remove(disk)

        if backing_file_relative_path or reuse_external_snapshot:
            libvirt.clean_up_snapshots(vm_name, domxml=vmxml_backup)
            if blk_source_folder:
                process.run("cd %s && rm -rf b c d" % blk_source_folder, shell=True)
        if disk_src_protocol == 'iscsi' or 'iscsi_target' in locals():
            libvirt.setup_or_cleanup_iscsi(is_setup=False,
                                           restart_tgtd=restart_tgtd)
        elif disk_src_protocol == 'gluster':
            gluster.setup_or_cleanup_gluster(False, brick_path=brick_path, **params)
            libvirtd = utils_libvirtd.Libvirtd()
            libvirtd.restart()
        elif disk_src_protocol == 'netfs':
            restore_selinux = params.get('selinux_status_bak')
            libvirt.setup_or_cleanup_nfs(is_setup=False,
                                         restore_selinux=restore_selinux)
        # Recover images xattr if having some
        dirty_images = get_images_with_xattr(vm)
        if dirty_images:
            clean_images_with_xattr(dirty_images)
            test.fail("VM's image(s) having xattr left")
