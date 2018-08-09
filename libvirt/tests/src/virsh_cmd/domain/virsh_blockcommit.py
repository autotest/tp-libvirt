import os
import logging
import tempfile
import collections

import aexpect

from avocado.utils import process

from virttest import virsh
from virttest import data_dir
from virttest import utils_libvirtd
from virttest import libvirt_storage
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt

from provider import libvirt_version


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


def run(test, params, env):
    """
    Test command: virsh blockcommit <domain> <path>

    1) Prepare test environment.
    2) Commit changes from a snapshot down to its backing image.
    3) Recover test environment.
    4) Check result.
    """

    def make_disk_snapshot(postfix_n, snapshot_take):
        """
        Make external snapshots for disks only.

        :param postfix_n: postfix option
        :param snapshot_take: snapshots taken.
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

            cmd_result = virsh.snapshot_create_as(vm_name, options,
                                                  ignore_status=True,
                                                  debug=True)
            status = cmd_result.exit_status
            if status != 0:
                test.fail("Failed to make snapshots for disks!")

            # Create a file flag in VM after each snapshot
            flag_file = tempfile.NamedTemporaryFile(prefix=("snapshot_test_"),
                                                    dir="/tmp")
            file_path = flag_file.name
            flag_file.close()

            status, output = session.cmd_status_output("touch %s" % file_path)
            if status:
                test.fail("Touch file in vm failed. %s" % output)
            snapshot_flag_files.append(file_path)

    def get_first_disk_source():
        """
        Get disk source of first device
        :return: first disk of first device.
        """
        first_device = vm.get_first_disk_devices()
        first_disk_src = first_device['source']
        return first_disk_src

    def make_relative_path_backing_files():
        """
        Create backing chain files of relative path.
        :return: absolute path of top active file
        """
        first_disk_source = get_first_disk_source()
        basename = os.path.basename(first_disk_source)
        root_dir = os.path.dirname(first_disk_source)
        cmd = "mkdir -p %s" % os.path.join(root_dir, '{b..d}')
        ret = process.run(cmd, shell=True)
        libvirt.check_exit_status(ret)

        # Make three external relative path backing files.
        backing_file_dict = collections.OrderedDict()
        backing_file_dict["b"] = "../%s" % basename
        backing_file_dict["c"] = "../b/b.img"
        backing_file_dict["d"] = "../c/c.img"
        for key, value in list(backing_file_dict.items()):
            backing_file_path = os.path.join(root_dir, key)
            cmd = ("cd %s && qemu-img create -f qcow2 -o backing_file=%s,backing_fmt=qcow2 %s.img"
                   % (backing_file_path, value, key))
            ret = process.run(cmd, shell=True)
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
    status_error = ("yes" == params.get("status_error", "no"))
    base_option = params.get("base_option", "none")
    middle_base = "yes" == params.get("middle_base", "no")
    pivot_opt = "yes" == params.get("pivot_opt", "no")
    snap_in_mirror = "yes" == params.get("snap_in_mirror", "no")
    snap_in_mirror_err = "yes" == params.get("snap_in_mirror_err", "no")
    with_active_commit = "yes" == params.get("with_active_commit", "no")
    multiple_chain = "yes" == params.get("multiple_chain", "no")
    virsh_dargs = {'debug': True}

    # Check whether qemu-img need add -U suboption since locking feature was added afterwards qemu-2.10
    qemu_img_locking_feature_support = libvirt_storage.check_qemu_image_lock_support()
    backing_file_relative_path = "yes" == params.get("backing_file_relative_path", "no")

    # Process domain disk device parameters
    disk_type = params.get("disk_type")
    disk_src_protocol = params.get("disk_source_protocol")
    restart_tgtd = params.get("restart_tgtd", 'no')
    vol_name = params.get("vol_name")
    tmp_dir = data_dir.get_tmp_dir()
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
    try:
        if disk_src_protocol == 'iscsi' and disk_type == 'network':
            if not libvirt_version.version_compare(1, 0, 4):
                test.cancel("'iscsi' disk doesn't support in"
                            " current libvirt version.")

        # Set vm xml and guest agent
        if replace_vm_disk:
            if disk_src_protocol == "rbd" and disk_type == "network":
                src_host = params.get("disk_source_host", "EXAMPLE_HOSTS")
                mon_host = params.get("mon_host", "EXAMPLE_MON_HOST")
                if src_host.count("EXAMPLE") or mon_host.count("EXAMPLE"):
                    test.cancel("Please provide rbd host first.")
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
            libvirt.set_vm_disk(vm, params, tmp_dir)

        if needs_agent:
            vm.prepare_guest_agent()

        # The first disk is supposed to include OS
        # We will perform blockcommit operation for it.
        first_disk = vm.get_first_disk_devices()
        blk_source = first_disk['source']
        blk_target = first_disk['target']
        snapshot_flag_files = []

        # get a vm session before snapshot
        session = vm.wait_for_login()
        # do snapshot
        postfix_n = 'snap'
        make_disk_snapshot(postfix_n, snapshot_take)

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
            # backingStore element introuduced in 1.2.4
            chain_lst = snap_src_lst[::-1]
            ret = check_chain_xml(disk_xml, chain_lst)
            if not ret:
                test.fail("Domain image backing chain check failed")

        # set blockcommit_options
        top_image = None
        blockcommit_options = "--wait --verbose"

        if with_timeout:
            blockcommit_options += " --timeout 1"

        if base_option == "shallow":
            blockcommit_options += " --shallow"
        elif base_option == "base":
            if middle_base:
                snap_name = "%s.%s1" % (diskname, postfix_n)
                blk_source = os.path.join(tmp_dir, snap_name)
            blockcommit_options += " --base %s" % blk_source

        if top_inactive:
            snap_name = "%s.%s2" % (diskname, postfix_n)
            top_image = os.path.join(tmp_dir, snap_name)
            blockcommit_options += " --top %s" % top_image
        else:
            blockcommit_options += " --active"
            if pivot_opt:
                blockcommit_options += " --pivot"

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
                blockcommit_options = "  --wait --verbose --top vda[1] --base vda[2] --keep-relative"
                block_commit_index = snapshot_take - 1
                expect_backing_file = True
            # Do block commit with --wait if top_inactive
            for count in range(1, block_commit_index):
                res = virsh.blockcommit(vm_name, blk_target,
                                        blockcommit_options, **virsh_dargs)
                libvirt.check_exit_status(res, status_error)
            check_chain_backing_files(blk_source_image, expect_backing_file)
            return

        # Run test case
        # Active commit does not support on rbd based disk with bug 1200726
        result = virsh.blockcommit(vm_name, blk_target,
                                   blockcommit_options, **virsh_dargs)

        # Check status_error
        libvirt.check_exit_status(result, status_error)
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
        # Recover xml of vm.
        vmxml_backup.sync("--snapshots-metadata")
        if cmd_session:
            cmd_session.close()
        for disk in snapshot_external_disks:
            if os.path.exists(disk):
                os.remove(disk)

        if backing_file_relative_path:
            libvirt.clean_up_snapshots(vm_name, domxml=vmxml_backup)
            process.run("cd %s && rm -rf b c d" % blk_source_folder, shell=True)
        if disk_src_protocol == 'iscsi':
            libvirt.setup_or_cleanup_iscsi(is_setup=False,
                                           restart_tgtd=restart_tgtd)
        elif disk_src_protocol == 'gluster':
            libvirt.setup_or_cleanup_gluster(False, vol_name, brick_path)
            libvirtd = utils_libvirtd.Libvirtd()
            libvirtd.restart()
        elif disk_src_protocol == 'netfs':
            restore_selinux = params.get('selinux_status_bak')
            libvirt.setup_or_cleanup_nfs(is_setup=False,
                                         restore_selinux=restore_selinux)
