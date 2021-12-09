import os
import logging
import tempfile
import collections

from avocado.utils import process

from virttest import virsh
from virttest import data_dir
from virttest import utils_libvirtd
from virttest import utils_misc
from virttest import utils_package
from virttest import libvirt_storage
from virttest import ceph
from virttest import gluster

from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml import snapshot_xml

from virttest import libvirt_version


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

    def make_disk_snapshot(snapshot_take):
        """
        Make external snapshots for disks only.

        :param snapshot_take: snapshots taken.
        """
        for count in range(1, snapshot_take + 1):
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

                # Skip cdrom
                if disk_xml.device == "cdrom":
                    continue
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
                if 'file' in disk_xml.source.attrs:
                    file_name = disk_xml.source.attrs['file']
                    new_file = "%s.snap%s" % (file_name.split('.')[0],
                                              count)
                    snapshot_external_disks.append(new_file)
                    new_attrs.update({'file': new_file})
                    hosts = None
                elif ('name' in disk_xml.source.attrs and
                      disk_src_protocol == 'gluster'):
                    src_name = disk_xml.source.attrs['name']
                    new_name = "%s.snap%s" % (src_name.split('.')[0],
                                              count)
                    new_attrs.update({'name': new_name})
                    snapshot_external_disks.append(new_name)
                    hosts = disk_xml.source.hosts
                elif ('dev' in disk_xml.source.attrs or
                      'name' in disk_xml.source.attrs):
                    if (disk_xml.type_name == 'block' or
                            disk_src_protocol in ['iscsi', 'rbd']):
                        # Use local file as external snapshot target for block
                        # and iscsi network type.
                        # As block device will be treat as raw format by
                        # default, it's not fit for external disk snapshot
                        # target. A work around solution is use qemu-img again
                        # with the target.
                        # And external active snapshots are not supported on
                        # 'network' disks using 'iscsi' protocol
                        disk_xml.type_name = 'file'
                        if 'dev' in new_attrs:
                            del new_attrs['dev']
                        elif 'name' in new_attrs:
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
                test.fail(snapshot_result.stderr)

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
        firt_disk_src = first_device['source']
        return firt_disk_src

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

    # Check whether qemu-img need add -U suboption since locking feature was added afterwards qemu-2.10
    qemu_img_locking_feature_support = libvirt_storage.check_qemu_image_lock_support()
    backing_file_relative_path = "yes" == params.get("backing_file_relative_path", "no")

    # Process domain disk device parameters
    disk_type = params.get("disk_type")
    disk_target = params.get("disk_target", 'vda')
    disk_src_protocol = params.get("disk_source_protocol")
    restart_tgtd = params.get("restart_tgtd", "no")
    vol_name = params.get("vol_name")
    tmp_dir = data_dir.get_data_dir()
    pool_name = params.get("pool_name", "gluster-pool")
    brick_path = os.path.join(tmp_dir, pool_name)

    # A backup of original vm
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    logging.debug("original xml is %s", vmxml_backup)

    # Abort the test if there are snapshots already
    exsiting_snaps = virsh.snapshot_list(vm_name)
    if len(exsiting_snaps) != 0:
        test.fail("There are snapshots created for %s already" % vm_name)

    snapshot_external_disks = []
    # Prepare a blank params to confirm if delete the configure at the end of the test
    ceph_cfg = ""
    try:
        if disk_src_protocol == 'iscsi' and disk_type == 'network':
            if not libvirt_version.version_compare(1, 0, 4):
                test.cancel("'iscsi' disk doesn't support in"
                            " current libvirt version.")
        if disk_src_protocol == 'gluster':
            if not libvirt_version.version_compare(1, 2, 7):
                test.cancel("Snapshot on glusterfs not"
                            " support in current "
                            "version. Check more info "
                            " with https://bugzilla.re"
                            "dhat.com/show_bug.cgi?id="
                            "1017289")

        # Set vm xml and guest agent
        if replace_vm_disk:
            if disk_src_protocol == "rbd" and disk_type == "network":
                src_host = params.get("disk_source_host", "EXAMPLE_HOSTS")
                mon_host = params.get("mon_host", "EXAMPLE_MON_HOST")
                # Create config file if it doesn't exist
                ceph_cfg = ceph.create_config_file(mon_host)
                if src_host.count("EXAMPLE") or mon_host.count("EXAMPLE"):
                    test.cancel("Please provide ceph host first.")

                params.update(
                   {"disk_source_name": os.path.join(
                      pool_name,
                      'rbd_blockpull_' + utils_misc.generate_random_string(4) +
                      '.img')})
                if utils_package.package_install(["ceph-common"]):
                    ceph.rbd_image_rm(
                        mon_host, *params.get("disk_source_name").split('/'))
                else:
                    test.error('Failed to install ceph-common package.')
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
        # We will perform blockpull operation for it.
        first_disk = vm.get_first_disk_devices()
        blk_source = first_disk['source']
        blk_target = first_disk['target']
        snapshot_flag_files = []

        # get a vm session before snapshot
        session = vm.wait_for_login()
        # do snapshot
        make_disk_snapshot(snapshot_take)

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
            # For libvirt is older version than 1.2.4 or source protocol is gluster
            # there are various base image,which depends on base option:shallow,base,top respectively
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

        if backing_file_relative_path:
            # Use block commit to shorten previous snapshots.
            blockcommit_options = "  --active --verbose --shallow --pivot --keep-relative"
            for count in range(1, snapshot_take + 1):
                res = virsh.blockcommit(vm_name, blk_target,
                                        blockcommit_options, **virsh_dargs)
                libvirt.check_exit_status(res, status_error)

            #Use block pull with --keep-relative flag,and reset base_index to 2.
            base_index = 2
            for count in range(1, snapshot_take):
                if count >= 3:
                    if libvirt_version.version_compare(6, 0, 0):
                        break
                    # If block pull operations are more than or equal to 3,
                    # it need reset base_index to 1. It only affects the test
                    # of libvirt < 6.0.0
                    base_index = 1
                base_image = "%s[%s]" % (disk_target, base_index)
                blockpull_options = "  --wait --verbose --base %s --keep-relative" % base_image
                res = virsh.blockpull(vm_name, blk_target,
                                      blockpull_options, **virsh_dargs)
                libvirt.check_exit_status(res, status_error)

                if libvirt_version.version_compare(6, 0, 0):
                    base_index += 1
            # Check final backing chain files.
            check_chain_backing_files(blk_source_image, True)
            return
        # Run test case
        result = virsh.blockpull(vm_name, blk_target,
                                 blockpull_options, **virsh_dargs)
        status = result.exit_status

        # If pull job aborted as timeout, the exit status is different
        # on RHEL6(0) and RHEL7(1)
        if with_timeout and 'Pull aborted' in result.stdout.strip():
            if libvirt_version.version_compare(1, 1, 1):
                status_error = True
            else:
                status_error = False

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
                        test.fail(err_msg)
                elif "base" or "shallow" in base_option:
                    if not base_index and base_image:
                        base_index = chain_lst.index(base_image)

                    chain_lst = snap_src_lst[:base_index][::-1]
                    if not libvirt_version.version_compare(6, 0, 0):
                        chain_lst = snap_src_lst[::-1][base_index:]
                    chain_lst.insert(0, snap_src_lst[-1])

                    ret = check_chain_xml(disk_xml, chain_lst)
                    if not ret:
                        test.fail(err_msg)

        # If base image is the top layer of snapshot chain,
        # virsh blockpull should fail, return directly
        if base_option == "top":
            return

        # Check flag files
        for flag in snapshot_flag_files:
            status, output = session.cmd_status_output("cat %s" % flag)
            if status:
                test.fail("blockpull failed: %s" % output)

    finally:
        # Remove ceph configure file if created
        if ceph_cfg:
            os.remove(ceph_cfg)
        if vm.is_alive():
            vm.destroy(gracefully=False)
        # Recover xml of vm.
        vmxml_backup.sync("--snapshots-metadata")
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

        if not disk_src_protocol or disk_src_protocol != 'gluster':
            for disk in snapshot_external_disks:
                if os.path.exists(disk):
                    os.remove(disk)

        if backing_file_relative_path:
            libvirt.clean_up_snapshots(vm_name, domxml=vmxml_backup)
            process.run("cd %s && rm -rf b c d" % blk_source_folder, shell=True)

        libvirtd = utils_libvirtd.Libvirtd()

        if disk_src_protocol == 'iscsi':
            libvirt.setup_or_cleanup_iscsi(is_setup=False,
                                           restart_tgtd=restart_tgtd)
        elif disk_src_protocol == 'gluster':
            logging.info("clean gluster env")
            gluster.setup_or_cleanup_gluster(False, brick_path=brick_path, **params)
            libvirtd.restart()
        elif disk_src_protocol == 'netfs':
            restore_selinux = params.get('selinux_status_bak')
            libvirt.setup_or_cleanup_nfs(is_setup=False,
                                         restore_selinux=restore_selinux)
