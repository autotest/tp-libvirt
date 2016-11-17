import os
import re
import time
import logging
import aexpect
from autotest.client.shared import error
from avocado.utils import process
from virttest import virsh
from virttest import utils_misc
from virttest import virt_vm, remote
from virttest import utils_libguestfs
from virttest import libvirt_storage
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml import vol_xml
from virttest.libvirt_xml import pool_xml
from virttest.libvirt_xml import secret_xml


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
                raise error.TestFail("Failed to define storage pool")
            if not sp.build_pool(pool_name):
                raise error.TestFail("Failed to build storage pool")
            if not sp.start_pool(pool_name):
                raise error.TestFail("Failed to start storage pool")

        # Check pool operation
        ret = virsh.pool_refresh(pool_name, **virsh_dargs)
        libvirt.check_exit_status(ret)
        ret = virsh.pool_uuid(pool_name, **virsh_dargs)
        libvirt.check_exit_status(ret)
        # pool-info
        pool_info = sp.pool_info(pool_name)
        if pool_info["Autostart"] != 'no':
            raise error.TestFail("Failed to check pool information")
        # pool-autostart
        if not sp.set_pool_autostart(pool_name):
            raise error.TestFail("Failed to set pool autostart")
        pool_info = sp.pool_info(pool_name)
        if pool_info["Autostart"] != 'yes':
            raise error.TestFail("Failed to check pool information")
        # pool-autostart --disable
        if not sp.set_pool_autostart(pool_name, "--disable"):
            raise error.TestFail("Failed to set pool autostart")
        # find-storage-pool-sources-as
        ret = virsh.find_storage_pool_sources_as("rbd", mon_host)
        libvirt.check_result(ret, unsupported_msg)

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
        Check volume infomation.
        """
        pv = libvirt_storage.PoolVolume(pool_name)
        # Supported operation
        if vol_name not in pv.list_volumes():
            raise error.TestFail("Volume %s doesn't exist" % vol_name)
        ret = virsh.vol_dumpxml(vol_name, pool_name)
        libvirt.check_exit_status(ret)
        # vol-info
        if not pv.volume_info(vol_name):
            raise error.TestFail("Can't see volmue info")
        # vol-key
        ret = virsh.vol_key(vol_name, pool_name)
        libvirt.check_exit_status(ret)
        if "%s/%s" % (disk_src_pool, vol_name) not in ret.stdout:
            raise error.TestFail("Volume key isn't correct")
        # vol-path
        ret = virsh.vol_path(vol_name, pool_name)
        libvirt.check_exit_status(ret)
        if "%s/%s" % (disk_src_pool, vol_name) not in ret.stdout:
            raise error.TestFail("Volume path isn't correct")
        # vol-pool
        ret = virsh.vol_pool("%s/%s" % (disk_src_pool, vol_name))
        libvirt.check_exit_status(ret)
        if pool_name not in ret.stdout:
            raise error.TestFail("Volume pool isn't correct")
        # vol-name
        ret = virsh.vol_name("%s/%s" % (disk_src_pool, vol_name))
        libvirt.check_exit_status(ret)
        if vol_name not in ret.stdout:
            raise error.TestFail("Volume name isn't correct")
        # vol-resize
        ret = virsh.vol_resize(vol_name, "2G", pool_name)
        libvirt.check_exit_status(ret)

        # Not supported operation
        # vol-clone
        ret = virsh.vol_clone(vol_name, "atest.vol", pool_name)
        libvirt.check_result(ret, unsupported_msg)
        # vol-create-from
        volxml = vol_xml.VolXML()
        vol_params.update({"name": "atest.vol"})
        v_xml = volxml.new_vol(**vol_params)
        v_xml.xmltreefile.write()
        ret = virsh.vol_create_from(pool_name, v_xml.xml, vol_name, pool_name)
        libvirt.check_result(ret, unsupported_msg)

        # vol-wipe
        ret = virsh.vol_wipe(vol_name, pool_name)
        libvirt.check_result(ret, unsupported_msg)
        # vol-upload
        ret = virsh.vol_upload(vol_name, vm.get_first_disk_devices()['source'],
                               "--pool %s" % pool_name)
        libvirt.check_result(ret, unsupported_msg)
        # vol-download
        ret = virsh.vol_download(vol_name, "atest.vol", "--pool %s" % pool_name)
        libvirt.check_result(ret, unsupported_msg)

    def check_qemu_cmd():
        """
        Check qemu command line options.
        """
        cmd = ("ps -ef | grep %s | grep -v grep " % vm_name)
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
            cmd += " | grep iothread=iothread%s" % driver_iothread
        # Run the command
        process.run(cmd, shell=True)

    def check_save_restore():
        """
        Test save and restore operation
        """
        save_file = os.path.join(test.tmpdir,
                                 "%s.save" % vm_name)
        ret = virsh.save(vm_name, save_file, **virsh_dargs)
        libvirt.check_exit_status(ret)
        ret = virsh.restore(save_file, **virsh_dargs)
        libvirt.check_exit_status(ret)
        if os.path.exists(save_file):
            os.remove(save_file)
        # Login to check vm status
        vm.wait_for_login().close()

    def check_snapshot(snap_option):
        """
        Test snapshot operation.
        """
        snap_name = "s1"
        snap_mem = os.path.join(test.tmpdir, "rbd.mem")
        snap_disk = os.path.join(test.tmpdir, "rbd.disk")
        expected_fails = []
        xml_snap_exp = ["disk name='vda' snapshot='external' type='file'"]
        xml_dom_exp = ["source file='%s'" % snap_disk,
                       "backingStore type='network' index='1'",
                       "source protocol='rbd' name='%s'" % disk_src_name]
        if snap_option.count("disk-only"):
            options = ("%s --diskspec vda,file=%s --disk-only" %
                       (snap_name, snap_disk))
        elif snap_option.count("disk-mem"):
            options = ("%s --memspec file=%s --diskspec vda,file="
                       "%s" % (snap_name, snap_mem, snap_disk))
            xml_snap_exp.append("memory snapshot='external' file='%s'"
                                % snap_mem)
        else:
            options = snap_name

        error_msg = params.get("error_msg")
        if error_msg:
            expected_fails.append(error_msg)
        ret = virsh.snapshot_create_as(vm_name, options)
        if ret.exit_status:
            libvirt.check_result(ret, expected_fails)

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
                raise error.TestFail("Failed to check snapshot xml")
            if not all([x in dom_xml for x in xml_dom_exp]):
                raise error.TestFail("Failed to check domain xml")

    def check_blockcopy(target):
        """
        Block copy operation test.
        """
        blk_file = os.path.join(test.tmpdir, "blk.rbd")
        if os.path.exists(blk_file):
            os.remove(blk_file)
        blk_mirror = ("mirror type='file' file='%s' "
                      "format='raw' job='copy'" % blk_file)

        # Do blockcopy
        ret = virsh.blockcopy(vm_name, target, blk_file)
        if ret.exit_status:
            error_msg = params.get("error_msg")
            if not error_msg:
                libvirt.check_exit_status(ret)
            else:
                libvirt.check_result(ret, [error_msg])
            # Passed error check, return
            return

        dom_xml = virsh.dumpxml(vm_name, debug=True).stdout.strip()
        if not dom_xml.count(blk_mirror):
            raise error.TestFail("Can't see block job in domain xml")

        # Abort
        ret = virsh.blockjob(vm_name, target, "--abort")
        libvirt.check_exit_status(ret)
        dom_xml = virsh.dumpxml(vm_name, debug=True).stdout.strip()
        if dom_xml.count(blk_mirror):
            raise error.TestFail("Failed to abort block job")
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
            raise error.TestFail("Failed to pivot block job")
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
            new_parts = libvirt.get_parts_list(session)
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

            if not added_part:
                logging.error("Cann't see added partition in VM")
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

        except (remote.LoginError, virt_vm.VMError, aexpect.ShellError), e:
            logging.error(str(e))
            return False

    mon_host = params.get("mon_host")
    disk_src_name = params.get("disk_source_name")
    disk_src_config = params.get("disk_source_config")
    disk_src_host = params.get("disk_source_host")
    disk_src_port = params.get("disk_source_port")
    disk_src_pool = params.get("disk_source_pool")
    disk_format = params.get("disk_format", "raw")
    driver_iothread = params.get("driver_iothread")
    pre_vm_state = params.get("pre_vm_state", "running")
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
    vol_cap = params.get("vol_cap")
    vol_cap_unit = params.get("vol_cap_unit")
    start_error_msg = params.get("start_error_msg")
    attach_error_msg = params.get("attach_error_msg")
    unsupported_msg = params.get("unsupported_msg")

    # Start vm and get all partions in vm.
    if vm.is_dead():
        vm.start()
    session = vm.wait_for_login()
    old_parts = libvirt.get_parts_list(session)
    session.close()
    vm.destroy(gracefully=False)
    if additional_guest:
        guest_name = "%s_%s" % (vm_name, '1')
        timeout = params.get("clone_timeout", 360)
        utils_libguestfs.virt_clone_cmd(vm_name, guest_name,
                                        True, timeout=timeout,
                                        ignore_status=False)
        additional_vm = vm.clone(guest_name)
        if pre_vm_state == "running":
            virsh.start(guest_name)

    # Back up xml file.
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    key_opt = ""
    secret_uuid = None
    key_file = os.path.join(test.tmpdir, "ceph.key")
    img_file = os.path.join(test.tmpdir,
                            "%s_test.img" % vm_name)

    try:
        # Set domain state
        libvirt.set_domain_state(vm, pre_vm_state)

        # Install ceph-common package which include rbd command
        if utils_misc.yum_install(["ceph-common"]):
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
                                         ret.stdout)[0].lstrip()
                logging.debug("Secret uuid %s", secret_uuid)
                if secret_uuid is None:
                    raise error.TestNAError("Failed to get secret uuid")

                # Set secret value
                auth_key = params.get("auth_key")
                ret = virsh.secret_set_value(secret_uuid, auth_key,
                                             **virsh_dargs)
                libvirt.check_exit_status(ret)

            # TODO - Delete the disk if it exists
            #cmd = ("rbd -m {0} {1} info {2} && rbd -m {0} {1} rm "
            #       "{2}".format(mon_host, key_opt, disk_src_name))
            #process.run(cmd, ignore_status=True, shell=True)
        else:
            raise error.TestNAError("Failed to install ceph-common")

        if disk_src_config:
            config_ceph()
        disk_path = ("rbd:%s:mon_host=%s" %
                     (disk_src_name, mon_host))
        if auth_user and auth_key:
            disk_path += (":id=%s:key=%s" %
                          (auth_user, auth_key))
        targetdev = params.get("disk_target", "vdb")
        # To be compatible with create_disk_xml function,
        # some parameters need to be updated.
        params.update({
            "type_name": params.get("disk_type", "network"),
            "target_bus": params.get("disk_target_bus"),
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
            disk_cmd = ("rbd -m %s %s info %s || qemu-img convert"
                        " -O %s %s %s" % (mon_host, key_opt,
                                          disk_src_name, disk_format,
                                          blk_source, disk_path))
            process.run(disk_cmd, ignore_status=False, shell=True)

        elif create_volume:
            vol_params = {"name": vol_name, "capacity": int(vol_cap),
                          "capacity_unit": vol_cap_unit, "format": "unknow"}

            create_pool()
            create_vol(vol_params)
            check_vol(vol_params)
        else:
            # Create an local image and make FS on it.
            disk_cmd = ("qemu-img create -f %s %s 10M && mkfs.ext4 -F %s" %
                        (disk_format, img_file, img_file))
            process.run(disk_cmd, ignore_status=False, shell=True)
            # Convert the image to remote storage
            disk_cmd = ("rbd -m %s %s info %s || qemu-img convert -O"
                        " %s %s %s" % (mon_host, key_opt, disk_src_name,
                                       disk_format, img_file, disk_path))
            process.run(disk_cmd, ignore_status=False, shell=True)
            # Create disk snapshot if needed.
            if create_snapshot:
                snap_cmd = ("rbd -m %s %s snap create %s@%s" %
                            (mon_host, key_opt, disk_src_name, snap_name))
                process.run(snap_cmd, ignore_status=False, shell=True)
        if attach_device:
            if create_volume:
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
            xml_file = libvirt.create_disk_xml(params)
            opts = params.get("attach_option", "")
            ret = virsh.attach_device(vm_name, xml_file,
                                      flagstr=opts, debug=True)
            if attach_error_msg:
                libvirt.check_result(ret, attach_error_msg)
            else:
                libvirt.check_exit_status(ret)
            if additional_guest:
                ret = virsh.attach_device(guest_name, xml_file,
                                          "", debug=True)
                libvirt.check_exit_status(ret)
        elif attach_disk:
            ret = virsh.attach_disk(vm_name, disk_path,
                                    targetdev, **virsh_dargs)
            libvirt.check_exit_status(ret)
        elif not create_volume:
            libvirt.set_vm_disk(vm, params)

        if pre_vm_state == "transient":
            logging.info("Creating %s...", vm_name)
            vmxml_for_test = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
            if vm.is_alive():
                vm.destroy(gracefully=False)
            vm.undefine()
            if virsh.create(vmxml_for_test.xml, **virsh_dargs).exit_status:
                vmxml_backup.define()
                raise error.TestFail("Cann't create the domain")
        elif vm.is_dead():
            vm.start()
        # Wait for vm is running
        vm.wait_for_login(timeout=600).close()
        if additional_guest:
            if additional_vm.is_dead():
                additional_vm.start()
        # Check qemu command line
        if test_qemu_cmd:
            check_qemu_cmd()
        # Check partitions in vm
        if test_vm_parts:
            if not check_in_vm(vm, targetdev, old_parts,
                               read_only=create_snapshot):
                raise error.TestFail("Failed to check vm partitions")
            if additional_guest:
                if not check_in_vm(additional_vm, targetdev, old_parts):
                    raise error.TestFail("Failed to check vm partitions")
        # Save and restore operation
        if test_save_restore:
            check_save_restore()
        if test_snapshot:
            snap_option = params.get("snapshot_option", "")
            check_snapshot(snap_option)
        if test_blockcopy:
            check_blockcopy(targetdev)

        # Detach the device.
        if attach_device and not attach_error_msg:
            xml_file = libvirt.create_disk_xml(params)
            ret = virsh.detach_device(vm_name, xml_file)
            libvirt.check_exit_status(ret)
            if additional_guest:
                ret = virsh.detach_device(guest_name, xml_file)
                libvirt.check_exit_status(ret)
        elif attach_disk:
            ret = virsh.detach_disk(vm_name, targetdev)
            libvirt.check_exit_status(ret)

        # Check disk in vm after detachment.
        if (attach_device or attach_disk) and not attach_error_msg:
            session = vm.wait_for_login()
            new_parts = libvirt.get_parts_list(session)
            if len(new_parts) != len(old_parts):
                raise error.TestFail("Disk still exists in vm"
                                     " after detachment")
            session.close()

    except virt_vm.VMStartError, details:
        if start_error_msg in str(details):
            pass
        else:
            raise error.TestFail("VM failed to start."
                                 "Error: %s" % str(details))
    finally:
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
        elif attach_device or attach_disk:
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
        if pool_name and virsh.pool_state_dict().has_key(pool_name):
            virsh.pool_destroy(pool_name, **virsh_dargs)
            virsh.pool_undefine(pool_name, **virsh_dargs)

        # Clean up secret
        if secret_uuid:
            virsh.secret_undefine(secret_uuid)

        logging.info("Restoring vm...")
        vmxml_backup.sync()
