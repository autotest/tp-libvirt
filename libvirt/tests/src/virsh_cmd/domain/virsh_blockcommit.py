import os
import logging
import tempfile
from autotest.client.shared import error
from autotest.client import utils
from virttest import virsh, data_dir
from virttest import aexpect
from virttest import utils_libvirtd
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.libvirt_xml.devices.disk import Disk
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

    def make_disk_snapshot(postfix_n):
        # Add all disks into commandline.
        disks = vm.get_disk_devices()

        # Make three external snapshots for disks only
        for count in range(1, 4):
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
                raise error.TestFail("Failed to make snapshots for disks!")

            # Create a file flag in VM after each snapshot
            flag_file = tempfile.NamedTemporaryFile(prefix=("snapshot_test_"),
                                                    dir="/tmp")
            file_path = flag_file.name
            flag_file.close()

            status, output = session.cmd_status_output("touch %s" % file_path)
            if status:
                raise error.TestFail("Touch file in vm failed. %s" % output)
            snapshot_flag_files.append(file_path)

    # MAIN TEST CODE ###
    # Process cartesian parameters
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
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

    # Process domain disk device parameters
    disk_type = params.get("disk_type")
    disk_src_protocol = params.get("disk_source_protocol")
    vol_name = params.get("vol_name")
    tmp_dir = data_dir.get_tmp_dir()
    pool_name = params.get("pool_name", "gluster-pool")
    brick_path = os.path.join(tmp_dir, pool_name)

    if not top_inactive:
        if not libvirt_version.version_compare(1, 2, 4):
            raise error.TestNAError("live active block commit is not supported"
                                    + " in current libvirt version.")

    # A backup of original vm
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    # Abort the test if there are snapshots already
    exsiting_snaps = virsh.snapshot_list(vm_name)
    if len(exsiting_snaps) != 0:
        raise error.TestFail("There are snapshots created for %s already" %
                             vm_name)

    snapshot_external_disks = []
    cmd_session = None
    try:
        if disk_src_protocol == 'iscsi' and disk_type == 'network':
            if not libvirt_version.version_compare(1, 0, 4):
                raise error.TestNAError("'iscsi' disk doesn't support in"
                                        + " current libvirt version.")

        # Set vm xml and guest agent
        if replace_vm_disk:
            libvirt.set_vm_disk(vm, params, tmp_dir)

        if needs_agent:
            libvirt.set_guest_agent(vm)

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
        make_disk_snapshot(postfix_n)

        basename = os.path.basename(blk_source)
        diskname = basename.split(".")[0]
        snap_src_lst = [blk_source]
        if multiple_chain:
            snap_name = "%s.%s1" % (diskname, postfix_n)
            snap_top = os.path.join(tmp_dir, snap_name)
            top_index = snapshot_external_disks.index(snap_top) + 1
            omit_list = snapshot_external_disks[top_index:]
            vm.destroy()
            vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
            disk_xml = vmxml.get_devices(device_type="disk")[0]
            vmxml.del_device(disk_xml)
            disk_dict = {'attrs': {'file': snap_top}}
            disk_xml.source = disk_xml.new_disk_source(**disk_dict)
            vmxml.add_device(disk_xml)
            vmxml.sync()
            vm.start()
            session = vm.wait_for_login()
            postfix_n = 'new_snap'
            make_disk_snapshot(postfix_n)
            snap_src_lst = [blk_source]
            snap_src_lst += snapshot_external_disks
            logging.debug("omit list is %s", omit_list)
            for i in omit_list:
                snap_src_lst.remove(i)
        else:
            # snapshot src file list
            snap_src_lst += snapshot_external_disks
        backing_chain = ''
        for i in reversed(range(4)):
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
                disk_xml = disk.xmltreefile
                logging.debug("the target disk xml after snapshot is %s",
                              disk_xml)
                break

        if not disk_xml:
            raise error.TestFail("Can't find disk xml with target %s" %
                                 blk_target)
        elif libvirt_version.version_compare(1, 2, 4):
            # backingStore element introuduced in 1.2.4
            chain_lst = snap_src_lst[::-1]
            ret = check_chain_xml(disk_xml, chain_lst)
            if not ret:
                raise error.TestFail("Domain image backing chain check failed")

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
            vm.shutdown()

        if with_active_commit:
            # inactive commit follow active commit will fail with bug 1135339
            cmd = "virsh blockcommit %s %s --active --pivot" % (vm_name,
                                                                blk_target)
            cmd_session = aexpect.ShellSession(cmd)

        # Run test case
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
                                    raise error.TestFail(err_msg)
                            else:
                                if disk_src_file != snap_src_lst[3]:
                                    raise error.TestFail(err_msg)
                        else:
                            if disk_src_file != blk_source:
                                raise error.TestFail(err_msg)
                        if libvirt_version.version_compare(1, 2, 7):
                            # The job attribute mentions which API started the
                            # operation since 1.2.7.
                            if job_type != 'active-commit':
                                raise error.TestFail("blockcommit job type '%s'"
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
                    if disk_mirror is None:
                        logging.debug(disk_xml)
                        if "--shallow" in blockcommit_options:
                            chain_lst = snap_src_lst[::-1]
                            chain_lst.pop(0)
                            ret = check_chain_xml(disk_xml, chain_lst)
                            if not ret:
                                raise error.TestFail("Domain image backing "
                                                     "chain check failed")
                        elif "--base" in blockcommit_options:
                            chain_lst = snap_src_lst[::-1]
                            base_index = chain_lst.index(blk_source)
                            chain_lst = chain_lst[base_index:]
                            ret = check_chain_xml(disk_xml, chain_lst)
                            if not ret:
                                raise error.TestFail("Domain image backing "
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
                            raise error.TestFail("Domain image backing chain "
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
                            raise error.TestFail("Domain image backing chain "
                                                 "check failed")
                    break
                else:
                    break

        # Check flag files
        if not vm_state == "shut off" and not multiple_chain:
            for flag in snapshot_flag_files:
                status, output = session.cmd_status_output("cat %s" % flag)
                if status:
                    raise error.TestFail("blockcommit failed: %s" % output)

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
            vm.destroy()
        # Recover xml of vm.
        vmxml_backup.sync("--snapshots-metadata")
        if cmd_session:
            cmd_session.close()
        for disk in snapshot_external_disks:
            if os.path.exists(disk):
                os.remove(disk)

        if disk_src_protocol == 'iscsi':
            libvirt.setup_or_cleanup_iscsi(is_setup=False)
        elif disk_src_protocol == 'gluster':
            libvirt.setup_or_cleanup_gluster(False, vol_name, brick_path)
            libvirtd = utils_libvirtd.Libvirtd()
            libvirtd.restart()
        elif disk_src_protocol == 'netfs':
            restore_selinux = params.get('selinux_status_bak')
            libvirt.setup_or_cleanup_nfs(is_setup=False,
                                         restore_selinux=restore_selinux)
