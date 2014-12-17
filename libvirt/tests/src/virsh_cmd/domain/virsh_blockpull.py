import os
import logging
import tempfile
from autotest.client.shared import error
from virttest import virsh, data_dir
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml
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
    Test command: virsh blockpull <domain> <path>

    1) Prepare test environment.
    2) Populate a disk from its backing image.
    3) Recover test environment.
    4) Check result.
    """

    def make_disk_snapshot():
        # Add all disks into commandline.
        disks = vm.get_disk_devices()

        # Make three external snapshots for disks only
        for count in range(1, 5):
            options = "snapshot%s snap%s-desc " \
                      "--disk-only --atomic --no-metadata" % (count, count)

            for disk in disks:
                disk_detail = disks[disk]
                basename = os.path.basename(disk_detail['source'])

                # Remove the original suffix if any, appending ".snap[0-9]"
                diskname = basename.split(".")[0]
                disk_external = os.path.join(tmp_dir,
                                             "%s.snap%s" % (diskname, count))

                snapshot_external_disks.append(disk_external)
                options += " %s,snapshot=external,file=%s" % (disk, disk_external)

            cmd_result = virsh.snapshot_create_as(vm_name, options,
                                                  ignore_status=True, debug=True)
            status = cmd_result.exit_status
            if status != 0:
                raise error.TestFail("Failed to make snapshots for disks!")

            # Create a file flag in VM after each snapshot
            flag_file = tempfile.NamedTemporaryFile(prefix=("snapshot_test_"), dir="/tmp")
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
    needs_agent = "yes" == params.get("needs_agent", "yes")
    replace_vm_disk = "yes" == params.get("replace_vm_disk", "no")

    with_timeout = ("yes" == params.get("with_timeout_option", "no"))
    status_error = ("yes" == params.get("status_error", "no"))
    base_option = params.get("base_option", None)
    keep_relative = "yes" == params.get("keep_relative", 'no')
    virsh_dargs = {'debug': True}

    # Process domain disk device parameters
    disk_type = params.get("disk_type")
    disk_src_protocol = params.get("disk_source_protocol")
    vol_name = params.get("vol_name")
    tmp_dir = data_dir.get_tmp_dir()
    pool_name = params.get("pool_name", "gluster-pool")
    brick_path = os.path.join(tmp_dir, pool_name)

    # A backup of original vm
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    logging.debug("original xml is %s", vmxml_backup)

    # Abort the test if there are snapshots already
    exsiting_snaps = virsh.snapshot_list(vm_name)
    if len(exsiting_snaps) != 0:
        raise error.TestFail("There are snapshots created for %s already" % vm_name)

    snapshot_external_disks = []
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
        # We will perform blockpull operation for it.
        first_disk = vm.get_first_disk_devices()
        blk_source = first_disk['source']
        blk_target = first_disk['target']
        snapshot_flag_files = []

        # get a vm session before snapshot
        session = vm.wait_for_login()
        # do snapshot
        make_disk_snapshot()

        # snapshot src file list
        snap_src_lst = [blk_source]
        snap_src_lst += snapshot_external_disks

        blockpull_options = "--wait --verbose"

        if with_timeout:
            blockpull_options += " --timeout 1"

        base_image = None
        basename = os.path.basename(blk_source)
        diskname = basename.split(".")[0]
        if base_option == "shallow":
            base_image = os.path.join(tmp_dir, "%s.snap3" % diskname)
        elif base_option == "base":
            base_image = os.path.join(tmp_dir, "%s.snap2" % diskname)
        elif base_option == "top":
            base_image = os.path.join(tmp_dir, "%s.snap4" % diskname)
        elif base_option == "async":
            blockpull_options += " --async"

        if base_option and base_image:
            blockpull_options += " --base %s" % base_image

        if keep_relative:
            blockpull_options += " --keep-relative"

        # Run test case
        result = virsh.blockpull(vm_name, blk_target,
                                 blockpull_options, **virsh_dargs)
        status = result.exit_status

        # Check status_error
        libvirt.check_exit_status(result, status_error)

        if not status and not with_timeout:
            vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
            disks = vmxml.devices.by_device_tag('disk')
            for disk in disks:
                if disk.target['dev'] != blk_target:
                    continue
                else:
                    disk_xml = disk.xmltreefile
                    break

            logging.debug("after pull the disk xml is: %s"
                          % disk_xml)
            if libvirt_version.version_compare(1, 2, 4):
                err_msg = "Domain image backing chain check failed"
                if not base_option:
                    chain_lst = snap_src_lst[-1:]
                    ret = check_chain_xml(disk_xml, chain_lst)
                    if not ret:
                        raise error.TestFail(err_msg)
                elif "shallow" in base_option:
                    chain_lst = snap_src_lst[::-1]
                    ret = check_chain_xml(disk_xml, chain_lst)
                    if not ret:
                        raise error.TestFail(err_msg)
                elif "base" in base_option:
                    chain_lst = snap_src_lst[::-1]
                    base_index = chain_lst.index(base_image)
                    val_tmp = []
                    for i in range(1, base_index):
                        val_tmp.append(chain_lst[i])
                    for i in val_tmp:
                        chain_lst.remove(i)
                    ret = check_chain_xml(disk_xml, chain_lst)
                    if not ret:
                        raise error.TestFail(err_msg)

        # If base image is the top layer of snapshot chain,
        # virsh blockpull should fail, return directly
        if base_option == "top":
            return

        # Check flag files
        for flag in snapshot_flag_files:
            status, output = session.cmd_status_output("cat %s" % flag)
            if status:
                raise error.TestFail("blockpull failed: %s" % output)

    finally:
        if vm.is_alive():
            vm.destroy(gracefully=False)
        # Recover xml of vm.
        vmxml_backup.sync("--snapshots-metadata")

        for disk in snapshot_external_disks:
            if os.path.exists(disk):
                os.remove(disk)

        if disk_src_protocol == 'iscsi':
            libvirt.setup_or_cleanup_iscsi(is_setup=False)
        elif disk_src_protocol == 'gluster':
            libvirt.setup_or_cleanup_gluster(False, vol_name, brick_path)
        elif disk_src_protocol == 'netfs':
            restore_selinux = params.get('selinux_status_bak')
            libvirt.setup_or_cleanup_nfs(is_setup=False,
                                         restore_selinux=restore_selinux)
