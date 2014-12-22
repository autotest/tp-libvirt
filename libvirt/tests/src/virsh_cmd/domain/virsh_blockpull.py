import os
import logging
import tempfile
from autotest.client.shared import error
from virttest import virsh, data_dir, utils_libvirtd
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml import snapshot_xml
from provider import libvirt_version


def check_chain_xml(disk_xml, chain_lst):
    """
    param disk_xml: disk xmltreefile
    param chain_lst: list, expected backing chain list
    return: True or False
    """
    logging.debug("expected backing chain list is %s", chain_lst)
    for elem in ('file', 'name', 'dev'):
        src_file = disk_xml.find('source').get(elem)
        if src_file:
            break
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
        # Make four external snapshots for disks only
        for count in range(1, 5):
            snap_xml = snapshot_xml.SnapshotXML()
            snapshot_name = "snapshot_test%s" % count
            snap_xml.snap_name = snapshot_name
            snap_xml.description = "Snapshot Test %s" % count

            # Add all disks into xml file.
            vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
            disks = vmxml.devices.by_device_tag('disk')
            new_disks = []
            for src_disk_xml in disks:
                disk_xml = snap_xml.SnapDiskXML()
                disk_xml.xmltreefile = src_disk_xml.xmltreefile
                del disk_xml.device
                del disk_xml.address
                disk_xml.snapshot = "external"
                disk_xml.disk_name = disk_xml.target['dev']

                # Only qcow2 works as external snapshot file format, update it
                # here
                driver_attr = disk_xml.driver
                driver_attr.update({'type': 'qcow2'})
                disk_xml.driver = driver_attr

                new_attrs = disk_xml.source.attrs
                if disk_xml.source.attrs.has_key('file'):
                    file_name = disk_xml.source.attrs['file']
                    new_file = "%s.snap%s" % (file_name.split('.')[0],
                                              count)
                    snapshot_external_disks.append(new_file)
                    new_attrs.update({'file': new_file})
                    hosts = None
                elif (disk_xml.source.attrs.has_key('name') and
                      disk_src_protocol == 'gluster'):
                    src_name = disk_xml.source.attrs['name']
                    new_name = "%s.snap%s" % (src_name.split('.')[0],
                                              count)
                    new_attrs.update({'name': new_name})
                    snapshot_external_disks.append(new_name)
                    hosts = disk_xml.source.hosts
                elif (disk_xml.source.attrs.has_key('dev') or
                      disk_xml.source.attrs.has_key('name')):
                    if (disk_xml.type_name == 'block' or
                            disk_src_protocol == 'iscsi'):
                        # Use local file as external snapshot target for block
                        # and iscsi network type.
                        # As block device will be treat as raw format by
                        # default, it's not fit for external disk snapshot
                        # target. A work around solution is use qemu-img again
                        # with the target.
                        # And external active snapshots are not supported on
                        # 'network' disks using 'iscsi' protocol
                        disk_xml.type_name = 'file'
                        if new_attrs.has_key('dev'):
                            del new_attrs['dev']
                        elif new_attrs.has_key('name'):
                            del new_attrs['name']
                            del new_attrs['protocol']
                        new_file = "%s/blk_src_file.snap%s" % (tmp_dir, count)
                        snapshot_external_disks.append(new_file)
                        new_attrs.update({'file': new_file})
                        hosts = None

                new_src_dict = {"attrs": new_attrs}
                if hosts:
                    new_src_dict.update({"hosts": hosts})
                disk_xml.source = disk_xml.new_disk_source(**new_src_dict)

                new_disks.append(disk_xml)

            snap_xml.set_disks(new_disks)
            snapshot_xml_path = snap_xml.xml
            logging.debug("The snapshot xml is: %s" % snap_xml.xmltreefile)

            options = "--disk-only --xmlfile %s " % snapshot_xml_path

            snapshot_result = virsh.snapshot_create(
                vm_name, options, debug=True)

            if snapshot_result.exit_status != 0:
                raise error.TestFail(snapshot_result.stderr)

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
    needs_agent = "yes" == params.get("needs_agent", "yes")
    replace_vm_disk = "yes" == params.get("replace_vm_disk", "no")
    snap_in_mirror = "yes" == params.get("snap_in_mirror", 'no')
    snap_in_mirror_err = "yes" == params.get("snap_in_mirror_err", 'no')
    bandwidth = params.get("bandwidth", None)
    with_timeout = ("yes" == params.get("with_timeout_option", "no"))
    status_error = ("yes" == params.get("status_error", "no"))
    base_option = params.get("base_option", None)
    keep_relative = "yes" == params.get("keep_relative", 'no')
    virsh_dargs = {'debug': True}

    # Process domain disk device parameters
    disk_type = params.get("disk_type")
    disk_target = params.get("disk_target", 'vda')
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
        if disk_src_protocol == 'gluster':
            if not libvirt_version.version_compare(1, 2, 7):
                raise error.TestNAError("Snapshot on glusterfs not"
                                        " support in current "
                                        "version. Check more info "
                                        " with https://bugzilla.re"
                                        "dhat.com/show_bug.cgi?id="
                                        "1017289")

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

        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        logging.debug("The domain xml after snapshot is %s" % vmxml)

        # snapshot src file list
        snap_src_lst = [blk_source]
        snap_src_lst += snapshot_external_disks

        if snap_in_mirror:
            blockpull_options = "--bandwidth 1"
        else:
            blockpull_options = "--wait --verbose"

        if with_timeout:
            blockpull_options += " --timeout 1"

        if bandwidth:
            blockpull_options += " --bandwidth %s" % bandwidth

        if base_option == "async":
            blockpull_options += " --async"

        base_image = None
        base_index = None
        if (libvirt_version.version_compare(1, 2, 4) or
                disk_src_protocol == 'gluster'):
            if base_option == "shallow":
                base_index = 1
                base_image = "%s[%s]" % (disk_target, base_index)
            elif base_option == "base":
                base_index = 2
                base_image = "%s[%s]" % (disk_target, base_index)
            elif base_option == "top":
                base_index = 0
                base_image = "%s[%s]" % (disk_target, base_index)
        else:
            if base_option == "shallow":
                base_image = snap_src_lst[3]
            elif base_option == "base":
                base_image = snap_src_lst[2]
            elif base_option == "top":
                base_image = snap_src_lst[4]

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
            if snap_in_mirror:
                snap_mirror_path = "%s/snap_mirror" % tmp_dir
                snap_options = "--diskspec vda,snapshot=external,"
                snap_options += "file=%s --disk-only" % snap_mirror_path
                snapshot_external_disks.append(snap_mirror_path)
                ret = virsh.snapshot_create_as(vm_name, snap_options,
                                               ignore_status=True,
                                               debug=True)
                libvirt.check_exit_status(ret, snap_in_mirror_err)
                return

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
                if not base_option or "async" in base_option:
                    chain_lst = snap_src_lst[-1:]
                    ret = check_chain_xml(disk_xml, chain_lst)
                    if not ret:
                        raise error.TestFail(err_msg)
                elif "base" or "shallow" in base_option:
                    chain_lst = snap_src_lst[::-1]
                    if not base_index and base_image:
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

        if not disk_src_protocol or disk_src_protocol != 'gluster':
            for disk in snapshot_external_disks:
                if os.path.exists(disk):
                    os.remove(disk)

        libvirtd = utils_libvirtd.Libvirtd()

        if disk_src_protocol == 'iscsi':
            libvirt.setup_or_cleanup_iscsi(is_setup=False)
        elif disk_src_protocol == 'gluster':
            libvirt.setup_or_cleanup_gluster(False, vol_name, brick_path)
            libvirtd.restart()
        elif disk_src_protocol == 'netfs':
            restore_selinux = params.get('selinux_status_bak')
            libvirt.setup_or_cleanup_nfs(is_setup=False,
                                         restore_selinux=restore_selinux)
