import os
import re
import time
import logging
import shutil
import aexpect

from collections import OrderedDict

from avocado.utils import process

from virttest import virsh
from virttest import utils_libvirtd
from virttest import utils_disk
from virttest import utils_misc
from virttest import utils_package
from virttest import virt_vm, remote
from virttest import utils_libguestfs
from virttest import libvirt_storage
from virttest import ceph

from virttest.utils_config import LibvirtQemuConfig
from virttest.utils_config import LibvirtSanLockConfig

from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml import vol_xml
from virttest.libvirt_xml import pool_xml
from virttest.libvirt_xml import secret_xml
from virttest.libvirt_xml.devices.lease import Lease
from virttest import data_dir

from virttest import libvirt_version

TMP_DATA_DIR = data_dir.get_data_dir()


def run(test, params, env):
    """
    Test rbd disk device.

    1.Prepare test environment,destroy or suspend a VM.
    2.Prepare disk image.
    3.Edit disks xml and start the domain.
    4.Perform test operation.
    5.Recover test environment.
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    virsh_dargs = {'debug': True, 'ignore_status': True}
    additional_xml_file = os.path.join(TMP_DATA_DIR, "additional_disk.xml")

    def config_ceph():
        """
        Write the configs to the file.
        """
        src_host = disk_src_host.split()
        src_port = disk_src_port.split()
        conf_str = "mon_host = "
        hosts = []
        for host, port in zip(src_host, src_port):
            hosts.append("%s:%s" % (host, port))
        with open(disk_src_config, 'w') as f:
            f.write(conf_str + ','.join(hosts) + '\n')

    def create_pool():
        """
        Define and start a pool.
        """
        sp = libvirt_storage.StoragePool()
        if create_by_xml:
            p_xml = pool_xml.PoolXML(pool_type=pool_type)
            p_xml.name = pool_name
            s_xml = pool_xml.SourceXML()
            s_xml.vg_name = disk_src_pool
            source_host = []
            for (host_name, host_port) in zip(
                    disk_src_host.split(), disk_src_port.split()):
                source_host.append({'name': host_name,
                                    'port': host_port})

            s_xml.hosts = source_host
            if auth_type:
                s_xml.auth_type = auth_type
            if auth_user:
                s_xml.auth_username = auth_user
            if auth_usage:
                s_xml.secret_usage = auth_usage
            p_xml.source = s_xml
            logging.debug("Pool xml: %s", p_xml)
            p_xml.xmltreefile.write()
            ret = virsh.pool_define(p_xml.xml, **virsh_dargs)
            libvirt.check_exit_status(ret)
            ret = virsh.pool_build(pool_name, **virsh_dargs)
            libvirt.check_exit_status(ret)
            ret = virsh.pool_start(pool_name, **virsh_dargs)
            libvirt.check_exit_status(ret)
        else:
            auth_opt = ""
            if client_name and client_key:
                auth_opt = ("--auth-type %s --auth-username %s --secret-usage '%s'"
                            % (auth_type, auth_user, auth_usage))
            if not sp.define_rbd_pool(pool_name, mon_host,
                                      disk_src_pool, extra=auth_opt):
                test.fail("Failed to define storage pool")
            if not sp.build_pool(pool_name):
                test.fail("Failed to build storage pool")
            if not sp.start_pool(pool_name):
                test.fail("Failed to start storage pool")

        # Check pool operation
        ret = virsh.pool_refresh(pool_name, **virsh_dargs)
        libvirt.check_exit_status(ret)
        ret = virsh.pool_uuid(pool_name, **virsh_dargs)
        libvirt.check_exit_status(ret)
        # pool-info
        pool_info = sp.pool_info(pool_name)
        if pool_info["Autostart"] != 'no':
            test.fail("Failed to check pool information")
        # pool-autostart
        if not sp.set_pool_autostart(pool_name):
            test.fail("Failed to set pool autostart")
        pool_info = sp.pool_info(pool_name)
        if pool_info["Autostart"] != 'yes':
            test.fail("Failed to check pool information")
        # pool-autostart --disable
        if not sp.set_pool_autostart(pool_name, "--disable"):
            test.fail("Failed to set pool autostart")
        # If port is not pre-configured, port value should not be hardcoded in pool information.
        if "yes" == params.get("rbd_port", "no"):
            if 'port' in virsh.pool_dumpxml(pool_name):
                test.fail("port attribute should not be in pool information")
        # find-storage-pool-sources-as
        if "yes" == params.get("find_storage_pool_sources_as", "no"):
            ret = virsh.find_storage_pool_sources_as("rbd", mon_host)
            libvirt.check_result(ret, skip_if=unsupported_err)

    def create_vol(vol_params):
        """
        Create volume.

        :param p_name. Pool name.
        :param vol_params. Volume parameters dict.
        :return: True if create successfully.
        """
        pvt = libvirt.PoolVolumeTest(test, params)
        if create_by_xml:
            pvt.pre_vol_by_xml(pool_name, **vol_params)
        else:
            pvt.pre_vol(vol_name, None, '2G', None, pool_name)

    def check_vol(vol_params):
        """
        Check volume information.
        """
        pv = libvirt_storage.PoolVolume(pool_name)
        # Supported operation
        if vol_name not in pv.list_volumes():
            test.fail("Volume %s doesn't exist" % vol_name)
        ret = virsh.vol_dumpxml(vol_name, pool_name)
        libvirt.check_exit_status(ret)
        # vol-info
        if not pv.volume_info(vol_name):
            test.fail("Can't see volume info")
        # vol-key
        ret = virsh.vol_key(vol_name, pool_name)
        libvirt.check_exit_status(ret)
        if "%s/%s" % (disk_src_pool, vol_name) not in ret.stdout.strip():
            test.fail("Volume key isn't correct")
        # vol-path
        ret = virsh.vol_path(vol_name, pool_name)
        libvirt.check_exit_status(ret)
        if "%s/%s" % (disk_src_pool, vol_name) not in ret.stdout.strip():
            test.fail("Volume path isn't correct")
        # vol-pool
        ret = virsh.vol_pool("%s/%s" % (disk_src_pool, vol_name))
        libvirt.check_exit_status(ret)
        if pool_name not in ret.stdout.strip():
            test.fail("Volume pool isn't correct")
        # vol-name
        ret = virsh.vol_name("%s/%s" % (disk_src_pool, vol_name))
        libvirt.check_exit_status(ret)
        if vol_name not in ret.stdout.strip():
            test.fail("Volume name isn't correct")
        # vol-resize
        ret = virsh.vol_resize(vol_name, "2G", pool_name)
        libvirt.check_exit_status(ret)

        # Not supported operation
        # vol-clone
        ret = virsh.vol_clone(vol_name, cloned_vol_name, pool_name)
        libvirt.check_result(ret, skip_if=unsupported_err)
        # vol-create-from
        volxml = vol_xml.VolXML()
        vol_params.update({"name": "%s" % create_from_cloned_volume})
        v_xml = volxml.new_vol(**vol_params)
        v_xml.xmltreefile.write()
        ret = virsh.vol_create_from(pool_name, v_xml.xml, vol_name, pool_name)
        libvirt.check_result(ret, skip_if=unsupported_err)

        # vol-wipe
        ret = virsh.vol_wipe(vol_name, pool_name)
        libvirt.check_result(ret, skip_if=unsupported_err)
        # vol-upload
        ret = virsh.vol_upload(vol_name, vm.get_first_disk_devices()['source'],
                               "--pool %s" % pool_name)
        libvirt.check_result(ret, skip_if=unsupported_err)
        # vol-download
        ret = virsh.vol_download(vol_name, cloned_vol_name, "--pool %s" % pool_name)
        libvirt.check_result(ret, skip_if=unsupported_err)

    def check_qemu_cmd():
        """
        Check qemu command line options.
        """
        cmd = ("ps -ef | grep %s | grep -v grep " % vm_name)
        process.run(cmd, shell=True)
        if disk_src_name:
            cmd += " | grep file=rbd:%s:" % disk_src_name
            if auth_user and auth_key:
                cmd += ('id=%s:auth_supported=cephx' % auth_user)
        if disk_src_config:
            cmd += " | grep 'conf=%s'" % disk_src_config
        elif mon_host:
            hosts = '\:6789\;'.join(mon_host.split())
            cmd += " | grep 'mon_host=%s'" % hosts
        if driver_iothread:
            cmd += " | grep iothread%s" % driver_iothread
        # Run the command
        process.run(cmd, shell=True)

    def check_save_restore():
        """
        Test save and restore operation
        """
        save_file = os.path.join(TMP_DATA_DIR,
                                 "%s.save" % vm_name)
        ret = virsh.save(vm_name, save_file, **virsh_dargs)
        libvirt.check_exit_status(ret)
        ret = virsh.restore(save_file, **virsh_dargs)
        libvirt.check_exit_status(ret)
        if os.path.exists(save_file):
            os.remove(save_file)
        # Login to check vm status
        vm.wait_for_login().close()

    def check_snapshot(snap_option, target_dev='vda'):
        """
        Test snapshot operation.
        """
        snap_name = "s1"
        snap_mem = os.path.join(TMP_DATA_DIR, "rbd.mem")
        snap_disk = os.path.join(TMP_DATA_DIR, "rbd.disk")
        xml_snap_exp = ["disk name='%s' snapshot='external' type='file'" % target_dev]
        xml_dom_exp = ["source file='%s'" % snap_disk,
                       "backingStore type='network' index='1'",
                       "source protocol='rbd' name='%s'" % disk_src_name]
        if snap_option.count("disk-only"):
            options = ("%s --diskspec %s,file=%s --disk-only" %
                       (snap_name, target_dev, snap_disk))
        elif snap_option.count("disk-mem"):
            options = ("%s --memspec file=%s --diskspec %s,file="
                       "%s" % (snap_name, snap_mem, target_dev, snap_disk))
            xml_snap_exp.append("memory snapshot='external' file='%s'"
                                % snap_mem)
        else:
            options = snap_name

        ret = virsh.snapshot_create_as(vm_name, options)
        if test_disk_internal_snapshot:
            libvirt.check_result(ret, expected_fails=unsupported_err)
        elif test_disk_readonly:
            if libvirt_version.version_compare(6, 0, 0):
                libvirt.check_result(ret)
            else:
                libvirt.check_result(ret, expected_fails=unsupported_err)
        else:
            libvirt.check_result(ret, skip_if=unsupported_err)

        # check xml file.
        if not ret.exit_status:
            snap_xml = virsh.snapshot_dumpxml(vm_name, snap_name,
                                              debug=True).stdout.strip()
            dom_xml = virsh.dumpxml(vm_name, debug=True).stdout.strip()
            # Delete snapshots.
            libvirt.clean_up_snapshots(vm_name)
            if os.path.exists(snap_mem):
                os.remove(snap_mem)
            if os.path.exists(snap_disk):
                os.remove(snap_disk)

            if not all([x in snap_xml for x in xml_snap_exp]):
                test.fail("Failed to check snapshot xml")
            if not all([x in dom_xml for x in xml_dom_exp]):
                test.fail("Failed to check domain xml")

    def check_blockcopy(target):
        """
        Block copy operation test.
        """
        blk_file = os.path.join(TMP_DATA_DIR, "blk.rbd")
        if os.path.exists(blk_file):
            os.remove(blk_file)
        blk_mirror = ("mirror type='file' file='%s' "
                      "format='raw' job='copy'" % blk_file)

        # Do blockcopy
        ret = virsh.blockcopy(vm_name, target, blk_file)
        libvirt.check_result(ret, skip_if=unsupported_err)

        dom_xml = virsh.dumpxml(vm_name, debug=True).stdout.strip()
        if not dom_xml.count(blk_mirror):
            test.fail("Can't see block job in domain xml")

        # Abort
        ret = virsh.blockjob(vm_name, target, "--abort")
        libvirt.check_exit_status(ret)
        dom_xml = virsh.dumpxml(vm_name, debug=True).stdout.strip()
        if dom_xml.count(blk_mirror):
            test.fail("Failed to abort block job")
        if os.path.exists(blk_file):
            os.remove(blk_file)

        # Sleep for a while after abort operation.
        time.sleep(5)
        # Do blockcopy again
        ret = virsh.blockcopy(vm_name, target, blk_file)
        libvirt.check_exit_status(ret)

        # Wait for complete
        def wait_func():
            ret = virsh.blockjob(vm_name, target, "--info")
            return ret.stderr.count("Block Copy: [100 %]")
        timeout = params.get("blockjob_timeout", 600)
        utils_misc.wait_for(wait_func, int(timeout))

        # Pivot
        ret = virsh.blockjob(vm_name, target, "--pivot")
        libvirt.check_exit_status(ret)
        dom_xml = virsh.dumpxml(vm_name, debug=True).stdout.strip()
        if not dom_xml.count("source file='%s'" % blk_file):
            test.fail("Failed to pivot block job")
        # Remove the disk file.
        if os.path.exists(blk_file):
            os.remove(blk_file)

    def check_in_vm(vm_obj, target, old_parts, read_only=False):
        """
        Check mount/read/write disk in VM.
        :param vm. VM guest.
        :param target. Disk dev in VM.
        :return: True if check successfully.
        """
        try:
            session = vm_obj.wait_for_login()
            new_parts = utils_disk.get_parts_list(session)
            added_parts = list(set(new_parts).difference(set(old_parts)))
            logging.info("Added parts:%s", added_parts)
            if len(added_parts) != 1:
                logging.error("The number of new partitions is invalid in VM")
                return False

            added_part = None
            if target.startswith("vd"):
                if added_parts[0].startswith("vd"):
                    added_part = added_parts[0]
            elif target.startswith("hd"):
                if added_parts[0].startswith("sd"):
                    added_part = added_parts[0]
            elif target.startswith("sd"):
                if added_parts[0].startswith("sd"):
                    added_part = added_parts[0]

            if not added_part:
                logging.error("Can't see added partition in VM")
                return False

            cmd = ("mount /dev/{0} /mnt && ls /mnt && (sleep 15;"
                   " touch /mnt/testfile; umount /mnt)"
                   .format(added_part))
            s, o = session.cmd_status_output(cmd, timeout=60)
            session.close()
            logging.info("Check disk operation in VM:\n, %s, %s", s, o)
            # Readonly fs, check the error messages.
            # The command may return True, read-only
            # messges can be found from the command output
            if read_only:
                if "Read-only file system" not in o:
                    return False
                else:
                    return True

            # Other errors
            if s != 0:
                return False
            return True

        except (remote.LoginError, virt_vm.VMError, aexpect.ShellError) as e:
            logging.error(str(e))
            return False

    def clean_up_volume_snapshots():
        """
        Get all snapshots for rbd_vol.img volume,unprotect and then clean up them.
        """
        cmd = ("rbd -m {0} {1} info {2}"
               "".format(mon_host, key_opt, os.path.join(disk_src_pool, vol_name)))
        if process.run(cmd, ignore_status=True, shell=True).exit_status:
            return
        # Get snapshot list.
        cmd = ("rbd -m {0} {1} snap"
               " list {2}"
               "".format(mon_host, key_opt, os.path.join(disk_src_pool, vol_name)))
        snaps_out = process.run(cmd, ignore_status=True, shell=True).stdout_text
        snap_names = []
        if snaps_out:
            for line in snaps_out.rsplit("\n"):
                if line.startswith("SNAPID") or line == "":
                    continue
                snap_line = line.rsplit()
                if len(snap_line) == 4:
                    snap_names.append(snap_line[1])
            logging.debug("Find snapshots: %s", snap_names)
            # Unprotect snapshot first,otherwise it will fail to purge volume
            for snap_name in snap_names:
                cmd = ("rbd -m {0} {1} snap"
                       " unprotect {2}@{3}"
                       "".format(mon_host, key_opt, os.path.join(disk_src_pool, vol_name), snap_name))
                process.run(cmd, ignore_status=True, shell=True)
        # Purge volume,and then delete volume.
        cmd = ("rbd -m {0} {1} info {2} && rbd -m {0} {1} snap"
               " purge {2} && rbd -m {0} {1} rm {2}"
               "".format(mon_host, key_opt, os.path.join(disk_src_pool, vol_name)))
        process.run(cmd, ignore_status=True, shell=True)

    def make_snapshot():
        """
        make external snapshots.

        :return external snapshot path list
        """
        logging.info("Making snapshot...")
        first_disk_source = vm.get_first_disk_devices()['source']
        snapshot_path_list = []
        snapshot2_file = os.path.join(TMP_DATA_DIR, "mem.s2")
        snapshot3_file = os.path.join(TMP_DATA_DIR, "mem.s3")
        snapshot4_file = os.path.join(TMP_DATA_DIR, "mem.s4")
        snapshot4_disk_file = os.path.join(TMP_DATA_DIR, "disk.s4")
        snapshot5_file = os.path.join(TMP_DATA_DIR, "mem.s5")
        snapshot5_disk_file = os.path.join(TMP_DATA_DIR, "disk.s5")

        # Attempt to take different types of snapshots.
        snapshots_param_dict = {"s1": "s1 --disk-only --no-metadata",
                                "s2": "s2 --memspec %s --no-metadata" % snapshot2_file,
                                "s3": "s3 --memspec %s --no-metadata --live" % snapshot3_file,
                                "s4": "s4 --memspec %s --diskspec vda,file=%s --no-metadata" % (snapshot4_file, snapshot4_disk_file),
                                "s5": "s5 --memspec %s --diskspec vda,file=%s --live --no-metadata" % (snapshot5_file, snapshot5_disk_file)}
        for snapshot_name in sorted(snapshots_param_dict.keys()):
            ret = virsh.snapshot_create_as(vm_name, snapshots_param_dict[snapshot_name],
                                           **virsh_dargs)
            libvirt.check_exit_status(ret)
            if snapshot_name != 's4' and snapshot_name != 's5':
                snapshot_path_list.append(first_disk_source.replace('qcow2', snapshot_name))
        return snapshot_path_list

    def get_secret_list():
        """
        Get secret list.

        :return secret list
        """
        logging.info("Get secret list ...")
        secret_list_result = virsh.secret_list()
        secret_list = secret_list_result.stdout_text.strip().splitlines()
        # First two lines contain table header followed by entries
        # for each secret, such as:
        #
        # UUID                                  Usage
        # --------------------------------------------------------------------------------
        # b4e8f6d3-100c-4e71-9f91-069f89742273  ceph client.libvirt secret
        secret_list = secret_list[2:]
        result = []
        # If secret list is empty.
        if secret_list:
            for line in secret_list:
                # Split on whitespace, assume 1 column
                linesplit = line.split(None, 1)
                result.append(linesplit[0])
        return result

    mon_host = params.get("mon_host")
    disk_src_name = params.get("disk_source_name")
    disk_src_config = params.get("disk_source_config")
    disk_src_host = params.get("disk_source_host")
    disk_src_port = params.get("disk_source_port")
    disk_src_pool = params.get("disk_source_pool")
    disk_format = params.get("disk_format", "raw")
    driver_iothread = params.get("driver_iothread")
    snap_name = params.get("disk_snap_name")
    attach_device = "yes" == params.get("attach_device", "no")
    attach_disk = "yes" == params.get("attach_disk", "no")
    test_save_restore = "yes" == params.get("test_save_restore", "no")
    test_snapshot = "yes" == params.get("test_snapshot", "no")
    test_blockcopy = "yes" == params.get("test_blockcopy", "no")
    test_qemu_cmd = "yes" == params.get("test_qemu_cmd", "no")
    test_vm_parts = "yes" == params.get("test_vm_parts", "no")
    additional_guest = "yes" == params.get("additional_guest", "no")
    create_snapshot = "yes" == params.get("create_snapshot", "no")
    convert_image = "yes" == params.get("convert_image", "no")
    create_volume = "yes" == params.get("create_volume", "no")
    rbd_blockcopy = "yes" == params.get("rbd_blockcopy", "no")
    enable_slice = "yes" == params.get("enable_slice", "no")
    create_by_xml = "yes" == params.get("create_by_xml", "no")
    client_key = params.get("client_key")
    client_name = params.get("client_name")
    auth_key = params.get("auth_key")
    auth_user = params.get("auth_user")
    auth_type = params.get("auth_type")
    auth_usage = params.get("secret_usage")
    pool_name = params.get("pool_name")
    pool_type = params.get("pool_type")
    vol_name = params.get("vol_name")
    cloned_vol_name = params.get("cloned_volume", "cloned_test_volume")
    create_from_cloned_volume = params.get("create_from_cloned_volume", "create_from_cloned_test_volume")
    vol_cap = params.get("vol_cap")
    vol_cap_unit = params.get("vol_cap_unit")
    start_vm = "yes" == params.get("start_vm", "no")
    test_disk_readonly = "yes" == params.get("test_disk_readonly", "no")
    test_disk_internal_snapshot = "yes" == params.get("test_disk_internal_snapshot", "no")
    test_disk_external_snapshot = "yes" == params.get("test_disk_external_snapshot", "no")
    test_json_pseudo_protocol = "yes" == params.get("json_pseudo_protocol", "no")
    disk_snapshot_with_sanlock = "yes" == params.get("disk_internal_with_sanlock", "no")
    auth_place_in_source = params.get("auth_place_in_source")
    test_target_bus = "yes" == params.get("scsi_target_test", "no")

    # Prepare a blank params to confirm if delete the configure at the end of the test
    ceph_cfg = ""
    # Create config file if it doesn't exist
    ceph_cfg = ceph.create_config_file(mon_host)

    # After libvirt 3.9.0, auth element can be put into source part.
    if auth_place_in_source and not libvirt_version.version_compare(3, 9, 0):
        test.cancel("place auth in source is not supported in current libvirt version")

    # After libvirt 6.0.0, blockcopy rbd backend feature is support.
    if rbd_blockcopy and not libvirt_version.version_compare(6, 0, 0):
        test.cancel("blockcopy rbd backend is not supported in current libvirt version")

    # Start vm and get all partions in vm.
    if vm.is_dead():
        vm.start()
    session = vm.wait_for_login()
    old_parts = utils_disk.get_parts_list(session)
    session.close()
    vm.destroy(gracefully=False)
    if additional_guest:
        guest_name = "%s_%s" % (vm_name, '1')
        timeout = params.get("clone_timeout", 360)
        utils_libguestfs.virt_clone_cmd(vm_name, guest_name,
                                        True, timeout=timeout,
                                        ignore_status=False)
        additional_vm = vm.clone(guest_name)
        if start_vm:
            virsh.start(guest_name)

    # Back up xml file.
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    key_opt = ""
    secret_uuid = None
    snapshot_path = None
    key_file = os.path.join(TMP_DATA_DIR, "ceph.key")
    img_file = os.path.join(TMP_DATA_DIR,
                            "%s_test.img" % vm_name)
    front_end_img_file = os.path.join(TMP_DATA_DIR,
                                      "%s_frontend_test.img" % vm_name)
    # Construct a unsupported error message list to skip these kind of tests
    unsupported_err = []
    if driver_iothread:
        unsupported_err.append('IOThreads not supported')
    if test_snapshot:
        unsupported_err.append('live disk snapshot not supported')
    if test_disk_readonly:
        if not libvirt_version.version_compare(5, 0, 0):
            unsupported_err.append('Could not create file: Permission denied')
            unsupported_err.append('Permission denied')
        else:
            unsupported_err.append('unsupported configuration: external snapshot ' +
                                   'for readonly disk vdb is not supported')
    if test_disk_internal_snapshot:
        unsupported_err.append('unsupported configuration: internal snapshot for disk ' +
                               'vdb unsupported for storage type raw')
    if test_blockcopy:
        unsupported_err.append('block copy is not supported')
    if attach_disk:
        unsupported_err.append('No such file or directory')
    if create_volume:
        unsupported_err.append("backing 'volume' disks isn't yet supported")
        unsupported_err.append('this function is not supported')

    try:
        # Clean up dirty secrets in test environments if there have.
        dirty_secret_list = get_secret_list()
        if dirty_secret_list:
            for dirty_secret_uuid in dirty_secret_list:
                virsh.secret_undefine(dirty_secret_uuid)
        # Prepare test environment.
        qemu_config = LibvirtQemuConfig()

        if disk_snapshot_with_sanlock:
            # Install necessary package:sanlock,libvirt-lock-sanlock
            if not utils_package.package_install(["sanlock"]):
                test.error("fail to install sanlock")
            if not utils_package.package_install(["libvirt-lock-sanlock"]):
                test.error("fail to install libvirt-lock-sanlock")

            # Set virt_use_sanlock
            result = process.run("setsebool -P virt_use_sanlock 1", shell=True)
            if result.exit_status:
                test.error("Failed to set virt_use_sanlock value")

            # Update lock_manager in qemu.conf
            qemu_config.lock_manager = 'sanlock'

            # Update qemu-sanlock.conf.
            san_lock_config = LibvirtSanLockConfig()
            san_lock_config.user = 'sanlock'
            san_lock_config.group = 'sanlock'
            san_lock_config.host_id = 1
            san_lock_config.auto_disk_leases = True
            process.run("mkdir -p /var/lib/libvirt/sanlock", shell=True)
            san_lock_config.disk_lease_dir = "/var/lib/libvirt/sanlock"
            san_lock_config.require_lease_for_disks = False

            # Start sanlock service and restart libvirtd to enforce changes.
            result = process.run("systemctl start wdmd", shell=True)
            if result.exit_status:
                test.error("Failed to start wdmd service")
            result = process.run("systemctl start sanlock", shell=True)
            if result.exit_status:
                test.error("Failed to start sanlock service")
            utils_libvirtd.Libvirtd().restart()

            # Prepare lockspace and lease file for sanlock in order.
            sanlock_cmd_dict = OrderedDict()
            sanlock_cmd_dict["truncate -s 1M /var/lib/libvirt/sanlock/TEST_LS"] = "Failed to truncate TEST_LS"
            sanlock_cmd_dict["sanlock direct init -s TEST_LS:0:/var/lib/libvirt/sanlock/TEST_LS:0"] = "Failed to sanlock direct init TEST_LS:0"
            sanlock_cmd_dict["chown sanlock:sanlock /var/lib/libvirt/sanlock/TEST_LS"] = "Failed to chown sanlock TEST_LS"
            sanlock_cmd_dict["restorecon -R -v /var/lib/libvirt/sanlock"] = "Failed to restorecon sanlock"
            sanlock_cmd_dict["truncate -s 1M /var/lib/libvirt/sanlock/test-disk-resource-lock"] = "Failed to truncate test-disk-resource-lock"
            sanlock_cmd_dict["sanlock direct init -r TEST_LS:test-disk-resource-lock:" +
                             "/var/lib/libvirt/sanlock/test-disk-resource-lock:0"] = "Failed to sanlock direct init test-disk-resource-lock"
            sanlock_cmd_dict["chown sanlock:sanlock " +
                             "/var/lib/libvirt/sanlock/test-disk-resource-lock"] = "Failed to chown test-disk-resource-loc"
            sanlock_cmd_dict["sanlock client add_lockspace -s TEST_LS:1:" +
                             "/var/lib/libvirt/sanlock/TEST_LS:0"] = "Failed to client add_lockspace -s TEST_LS:0"
            for sanlock_cmd in sanlock_cmd_dict.keys():
                result = process.run(sanlock_cmd, shell=True)
                if result.exit_status:
                    test.error(sanlock_cmd_dict[sanlock_cmd])

            # Create one lease device and add it to VM.
            san_lock_vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
            lease_device = Lease()
            lease_device.lockspace = 'TEST_LS'
            lease_device.key = 'test-disk-resource-lock'
            lease_device.target = {'path': '/var/lib/libvirt/sanlock/test-disk-resource-lock'}
            san_lock_vmxml.add_device(lease_device)
            san_lock_vmxml.sync()

        # Install ceph-common package which include rbd command
        if utils_package.package_install(["ceph-common"]):
            if client_name and client_key:
                with open(key_file, 'w') as f:
                    f.write("[%s]\n\tkey = %s\n" %
                            (client_name, client_key))
                key_opt = "--keyring %s" % key_file

                # Create secret xml
                sec_xml = secret_xml.SecretXML("no", "no")
                sec_xml.usage = auth_type
                sec_xml.usage_name = auth_usage
                sec_xml.xmltreefile.write()

                logging.debug("Secret xml: %s", sec_xml)
                ret = virsh.secret_define(sec_xml.xml)
                libvirt.check_exit_status(ret)

                secret_uuid = re.findall(r".+\S+(\ +\S+)\ +.+\S+",
                                         ret.stdout.strip())[0].lstrip()
                logging.debug("Secret uuid %s", secret_uuid)
                if secret_uuid is None:
                    test.error("Failed to get secret uuid")

                # Set secret value
                auth_key = params.get("auth_key")
                ret = virsh.secret_set_value(secret_uuid, auth_key,
                                             **virsh_dargs)
                libvirt.check_exit_status(ret)

            # Delete the disk if it exists
            cmd = ("rbd -m {0} {1} info {2} && rbd -m {0} {1} rm "
                   "{2}".format(mon_host, key_opt, disk_src_name))
            process.run(cmd, ignore_status=True, shell=True)
        else:
            test.error("Failed to install ceph-common")

        if disk_src_config:
            config_ceph()
        disk_path = ("rbd:%s:mon_host=%s" %
                     (disk_src_name, mon_host))
        if auth_user and auth_key:
            disk_path += (":id=%s:key=%s" %
                          (auth_user, auth_key))
        targetdev = params.get("disk_target", "vdb")
        targetbus = params.get("disk_target_bus", "virtio")
        if test_target_bus:
            targetdev = params.get("disk_target", "sdb")
            targetbus = params.get("disk_target_bus", "scsi")
            # Add virtio-scsi controller for sd* test
            controller_vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
            condict = {'controller_type': targetbus}
            ctrl = libvirt.create_controller_xml(condict)
            libvirt.add_controller(vm.name, ctrl)
            logging.debug("Controller XML is:%s", ctrl)
        # To be compatible with create_disk_xml function,
        # some parameters need to be updated.
        params.update({
            "type_name": params.get("disk_type", "network"),
            "target_bus": targetbus,
            "target_dev": targetdev,
            "secret_uuid": secret_uuid,
            "source_protocol": params.get("disk_source_protocol"),
            "source_name": disk_src_name,
            "source_host_name": disk_src_host,
            "source_host_port": disk_src_port})
        # Prepare disk image
        if convert_image:
            first_disk = vm.get_first_disk_devices()
            blk_source = first_disk['source']
            # Convert the image to remote storage
            disk_cmd = ("rbd -m %s %s info %s 2> /dev/null|| qemu-img convert"
                        " -O %s %s %s" % (mon_host, key_opt,
                                          disk_src_name, disk_format,
                                          blk_source, disk_path))
            process.run(disk_cmd, ignore_status=False, shell=True)

        elif create_volume:
            vol_params = {"name": vol_name, "capacity": int(vol_cap),
                          "capacity_unit": vol_cap_unit, "format": disk_format}

            create_pool()
            create_vol(vol_params)
            check_vol(vol_params)
        elif rbd_blockcopy:
            # Create one disk to attach to VM as second disk device
            second_disk_params = {}
            disk_size = params.get("virt_disk_device_size", "50M")
            device_source = libvirt.create_local_disk(
                "file", img_file, disk_size, disk_format="qcow2")
            second_disk_params.update({"source_file": device_source})
            second_disk_params.update({"driver_type": "qcow2"})
            second_xml_file = libvirt.create_disk_xml(second_disk_params)
            opts = params.get("attach_option", "--config")
            ret = virsh.attach_device(vm_name, second_xml_file,
                                      flagstr=opts, debug=True)
            libvirt.check_result(ret)
        else:
            # Create an local image and make FS on it.
            disk_cmd = ("qemu-img create -f %s %s 10M && mkfs.ext4 -F %s" %
                        (disk_format, img_file, img_file))
            process.run(disk_cmd, ignore_status=False, shell=True)
            # Convert the image to remote storage
            disk_cmd = ("rbd -m %s %s info %s 2> /dev/null|| qemu-img convert -O"
                        " %s %s %s" % (mon_host, key_opt, disk_src_name,
                                       disk_format, img_file, disk_path))
            process.run(disk_cmd, ignore_status=False, shell=True)
            # Create disk snapshot if needed.
            if create_snapshot:
                snap_cmd = ("rbd -m %s %s snap create %s@%s" %
                            (mon_host, key_opt, disk_src_name, snap_name))
                process.run(snap_cmd, ignore_status=False, shell=True)
            if test_json_pseudo_protocol:
                # After block-dev introduced, qemu-img: warning: RBD options encoded in the filename as keyvalue pairs is deprecated
                if libvirt_version.version_compare(6, 0, 0):
                    test.cancel("qemu-img: warning: RBD options encoded in the filename as keyvalue pairs in json format is deprecated")
                # Create one frontend image with the rbd backing file.
                json_str = ('json:{"file.driver":"rbd",'
                            '"file.filename":"rbd:%s:mon_host=%s"}'
                            % (disk_src_name, mon_host))
                # pass different json string according to the auth config
                if auth_user and auth_key:
                    json_str = ('%s:id=%s:key=%s"}' % (json_str[:-2], auth_user, auth_key))
                disk_cmd = ("qemu-img create -f qcow2 -b '%s' %s" %
                            (json_str, front_end_img_file))
                disk_path = front_end_img_file
                process.run(disk_cmd, ignore_status=False, shell=True)
        # If hot plug, start VM first, and then wait the OS boot.
        # Otherwise stop VM if running.
        if start_vm:
            if vm.is_dead():
                vm.start()
            vm.wait_for_login().close()
        else:
            if not vm.is_dead():
                vm.destroy()
        if attach_device:
            if create_volume:
                params.update({"source_pool": pool_name})
                params.update({"type_name": "volume"})
                # No need auth options for volume
                if "auth_user" in params:
                    params.pop("auth_user")
                if "auth_type" in params:
                    params.pop("auth_type")
                if "secret_type" in params:
                    params.pop("secret_type")
                if "secret_uuid" in params:
                    params.pop("secret_uuid")
                if "secret_usage" in params:
                    params.pop("secret_usage")
            # After 3.9.0,the auth element can be place in source part.
            if auth_place_in_source:
                params.update({"auth_in_source": auth_place_in_source})
            xml_file = libvirt.create_disk_xml(params)
            if additional_guest:
                # Copy xml_file for additional guest VM.
                shutil.copyfile(xml_file, additional_xml_file)
            opts = params.get("attach_option", "")
            ret = virsh.attach_device(vm_name, xml_file,
                                      flagstr=opts, debug=True)
            libvirt.check_result(ret, skip_if=unsupported_err)
            if additional_guest:
                # Make sure the additional VM is running
                if additional_vm.is_dead():
                    additional_vm.start()
                    additional_vm.wait_for_login().close()
                ret = virsh.attach_device(guest_name, additional_xml_file,
                                          "", debug=True)
                libvirt.check_result(ret, skip_if=unsupported_err)
        elif attach_disk:
            opts = params.get("attach_option", "")
            ret = virsh.attach_disk(vm_name, disk_path,
                                    targetdev, opts)
            libvirt.check_result(ret, skip_if=unsupported_err)
        elif test_disk_readonly:
            params.update({'readonly': "yes"})
            xml_file = libvirt.create_disk_xml(params)
            opts = params.get("attach_option", "")
            ret = virsh.attach_device(vm_name, xml_file,
                                      flagstr=opts, debug=True)
            libvirt.check_result(ret, skip_if=unsupported_err)
        elif test_disk_internal_snapshot:
            xml_file = libvirt.create_disk_xml(params)
            opts = params.get("attach_option", "")
            ret = virsh.attach_device(vm_name, xml_file,
                                      flagstr=opts, debug=True)
            libvirt.check_result(ret, skip_if=unsupported_err)
        elif disk_snapshot_with_sanlock:
            if vm.is_dead():
                vm.start()
            snapshot_path = make_snapshot()
            if vm.is_alive():
                vm.destroy()
        elif rbd_blockcopy:
            if enable_slice:
                disk_cmd = ("rbd -m %s %s create %s --size 400M 2> /dev/null"
                            % (mon_host, key_opt, disk_src_name))
                process.run(disk_cmd, ignore_status=False, shell=True)
                slice_dict = {"slice_type": "storage", "slice_offset": "12345", "slice_size": "105185280"}
                params.update({"disk_slice": slice_dict})
                logging.debug('create one volume on ceph backend storage for slice testing')
            # Create one file on VM before doing blockcopy
            try:
                session = vm.wait_for_login()
                cmd = ("mkfs.ext4 -F /dev/{0} && mount /dev/{0} /mnt && ls /mnt && (sleep 3;"
                       " touch /mnt/rbd_blockcopyfile; umount /mnt)"
                       .format(targetdev))
                s, o = session.cmd_status_output(cmd, timeout=60)
                session.close()
                logging.info("touch one file in new added disk in VM:\n, %s, %s", s, o)
            except (remote.LoginError, virt_vm.VMError, aexpect.ShellError) as e:
                logging.error(str(e))
            # Create rbd backend xml
            rbd_blockcopy_xml_file = libvirt.create_disk_xml(params)
            logging.debug("The rbd blockcopy xml is: %s" % rbd_blockcopy_xml_file)
            dest_path = " --xml %s" % rbd_blockcopy_xml_file
            options1 = params.get("rbd_pivot_option", " --wait --verbose --transient-job --pivot")
            extra_dict = {'debug': True}
            cmd_result = virsh.blockcopy(vm_name, targetdev,
                                         dest_path, options1,
                                         **extra_dict)
            libvirt.check_exit_status(cmd_result)
        elif not create_volume:
            libvirt.set_vm_disk(vm, params)
        if test_blockcopy:
            logging.info("Creating %s...", vm_name)
            vmxml_for_test = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
            if vm.is_alive():
                vm.destroy(gracefully=False)
            vm.undefine()
            if virsh.create(vmxml_for_test.xml, **virsh_dargs).exit_status:
                vmxml_backup.define()
                test.fail("Can't create the domain")
        elif vm.is_dead():
            vm.start()
        # Wait for vm is running
        vm.wait_for_login(timeout=600).close()
        if additional_guest:
            if additional_vm.is_dead():
                additional_vm.start()

        # After block-dev introduced in libvirt 6.0.0 afterwards, file=rbd:* format information is not provided from qemu output
        if libvirt_version.version_compare(6, 0, 0):
            test_qemu_cmd = False

        # Check qemu command line
        if test_qemu_cmd:
            check_qemu_cmd()
        # Check partitions in vm
        if test_vm_parts:
            if not check_in_vm(vm, targetdev, old_parts,
                               read_only=create_snapshot):
                test.fail("Failed to check vm partitions")
            if additional_guest:
                if not check_in_vm(additional_vm, targetdev, old_parts):
                    test.fail("Failed to check vm partitions")
        # Save and restore operation
        if test_save_restore:
            check_save_restore()
        if test_snapshot:
            snap_option = params.get("snapshot_option", "")
            check_snapshot(snap_option)
        if test_blockcopy:
            check_blockcopy(targetdev)
        if test_disk_readonly and not libvirt_version.version_compare(6, 0, 0):
            snap_option = params.get("snapshot_option", "")
            check_snapshot(snap_option, 'vdb')
        if test_disk_internal_snapshot:
            snap_option = params.get("snapshot_option", "")
            check_snapshot(snap_option, targetdev)
        if test_disk_external_snapshot:
            snap_option = params.get("snapshot_option", "")
            check_snapshot(snap_option, targetdev)
        # Check rbd blockcopy inside VM
        if rbd_blockcopy:
            try:
                session = vm.wait_for_login()
                cmd = ("mount /dev/{0} /mnt && ls /mnt/rbd_blockcopyfile && (sleep 3;"
                       " umount /mnt)"
                       .format(targetdev))
                s, o = session.cmd_status_output(cmd, timeout=60)
                session.close()
                logging.info("list one file in new rbd backend disk in VM:\n, %s, %s", s, o)
            except (remote.LoginError, virt_vm.VMError, aexpect.ShellError) as e:
                logging.error(str(e))
            debug_vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)

            def _check_slice_in_xml():
                """
                Check slice attribute in disk xml.
                """
                debug_vmxml = virsh.dumpxml(vm_name, "", debug=True).stdout.strip()
                if 'slices' in debug_vmxml:
                    return True
                else:
                    return False
            if enable_slice:
                if not _check_slice_in_xml():
                    test.fail("Failed to find slice attribute in VM xml")
        # Detach the device.
        if attach_device:
            xml_file = libvirt.create_disk_xml(params)
            ret = virsh.detach_device(vm_name, xml_file, wait_remove_event=True)
            libvirt.check_exit_status(ret)
            if additional_guest:
                ret = virsh.detach_device(guest_name, xml_file, wait_remove_event=True)
                libvirt.check_exit_status(ret)
        elif attach_disk:
            ret = virsh.detach_disk(vm_name, targetdev, wait_remove_event=True)
            libvirt.check_exit_status(ret)

        # Check disk in vm after detachment.
        if attach_device or attach_disk:
            session = vm.wait_for_login()
            new_parts = utils_disk.get_parts_list(session)
            if len(new_parts) != len(old_parts):
                test.fail("Disk still exists in vm"
                          " after detachment")
            session.close()

    except virt_vm.VMStartError as details:
        for msg in unsupported_err:
            if msg in str(details):
                test.cancel(str(details))
        else:
            test.fail("VM failed to start."
                      "Error: %s" % str(details))
    finally:
        # Remove ceph configure file if created.
        if ceph_cfg:
            os.remove(ceph_cfg)
        # Delete snapshots.
        snapshot_lists = virsh.snapshot_list(vm_name)
        if len(snapshot_lists) > 0:
            libvirt.clean_up_snapshots(vm_name, snapshot_lists)
            for snap in snapshot_lists:
                virsh.snapshot_delete(vm_name, snap, "--metadata")

        # Recover VM.
        if vm.is_alive():
            vm.destroy(gracefully=False)
        if additional_guest:
            virsh.remove_domain(guest_name,
                                "--remove-all-storage",
                                ignore_stauts=True)
        # Remove the snapshot.
        if create_snapshot:
            cmd = ("rbd -m {0} {1} info {2} && rbd -m {0} {1} snap"
                   " purge {2} && rbd -m {0} {1} rm {2}"
                   "".format(mon_host, key_opt, disk_src_name))
            process.run(cmd, ignore_status=True, shell=True)
        elif create_volume:
            cmd = ("rbd -m {0} {1} info {2} && rbd -m {0} {1} rm {2}"
                   "".format(mon_host, key_opt, os.path.join(disk_src_pool, cloned_vol_name)))
            process.run(cmd, ignore_status=True, shell=True)
            cmd = ("rbd -m {0} {1} info {2} && rbd -m {0} {1} rm {2}"
                   "".format(mon_host, key_opt, os.path.join(disk_src_pool, create_from_cloned_volume)))
            process.run(cmd, ignore_status=True, shell=True)
            clean_up_volume_snapshots()
        else:
            cmd = ("rbd -m {0} {1} info {2} && rbd -m {0} {1} rm {2}"
                   "".format(mon_host, key_opt, disk_src_name))
            process.run(cmd, ignore_status=True, shell=True)

        # Delete tmp files.
        if os.path.exists(key_file):
            os.remove(key_file)
        if os.path.exists(img_file):
            os.remove(img_file)
        # Clean up volume, pool
        if vol_name and vol_name in str(virsh.vol_list(pool_name).stdout):
            virsh.vol_delete(vol_name, pool_name)
        if pool_name and pool_name in virsh.pool_state_dict():
            virsh.pool_destroy(pool_name, **virsh_dargs)
            virsh.pool_undefine(pool_name, **virsh_dargs)

        # Clean up secret
        secret_list = get_secret_list()
        if secret_list:
            for secret_uuid in secret_list:
                virsh.secret_undefine(secret_uuid)

        logging.info("Restoring vm...")
        vmxml_backup.sync()

        if disk_snapshot_with_sanlock:
            # Restore virt_use_sanlock setting.
            process.run("setsebool -P virt_use_sanlock 0", shell=True)
            # Restore qemu config
            qemu_config.restore()
            utils_libvirtd.Libvirtd().restart()
            # Force shutdown sanlock service.
            process.run("sanlock client shutdown -f 1", shell=True)
            # Clean up lockspace folder
            process.run("rm -rf  /var/lib/libvirt/sanlock/*", shell=True)
            if snapshot_path is not None:
                for snapshot in snapshot_path:
                    if os.path.exists(snapshot):
                        os.remove(snapshot)
