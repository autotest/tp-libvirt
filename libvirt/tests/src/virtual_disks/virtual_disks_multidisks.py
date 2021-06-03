import os
import re
import time
import base64
import json
import logging
import platform
import aexpect
import locale

from avocado.utils import process

from virttest import virt_vm
from virttest import virsh
from virttest import remote
from virttest import nfs
from virttest import utils_libvirtd
from virttest import utils_misc
from virttest import utils_disk
from virttest import data_dir
from virttest import utils_selinux
from virttest import utils_package

from virttest.utils_test import libvirt
from virttest.utils_config import LibvirtQemuConfig
from virttest.utils_config import LibvirtdConfig

from virttest.libvirt_xml import vm_xml, xcepts
from virttest.libvirt_xml.devices.disk import Disk
from virttest.libvirt_xml.devices.input import Input
from virttest.libvirt_xml.devices.hub import Hub
from virttest.libvirt_xml.devices.controller import Controller
from virttest.libvirt_xml.devices.address import Address
from virttest.libvirt_xml import secret_xml
from virttest.libvirt_xml import pool_xml
from virttest.staging import lv_utils
from virttest.utils_libvirt import libvirt_pcicontr

from virttest import libvirt_version


def run(test, params, env):
    """
    Test multiple disks attachment.

    1.Prepare test environment,destroy or suspend a VM.
    2.Perform 'qemu-img create' operation.
    3.Edit disks xml and start the domain.
    4.Perform test operation.
    5.Recover test environment.
    6.Confirm the test result.
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    virsh_dargs = {'debug': True, 'ignore_status': True}
    # Indicate to the PPC platform
    on_ppc = False
    if platform.platform().count('ppc64'):
        on_ppc = True
    device_source = params.get("device_source", '/dev/sdb')
    pvt = libvirt.PoolVolumeTest(test, params)
    tmp_demo_img = "/tmp/demo.img"
    se_obj = None
    arch = params.get("vm_arch_name", "x86_64")
    machine = params.get("machine_type", "pc")

    original_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    disk_devices = original_xml.get_devices('disk')
    for disk in disk_devices:
        if disk.device != 'disk':
            virsh.detach_disk(vm_name, disk.target['dev'],
                              extra='--config', debug=True)

    def check_disk_order(targets_name):
        """
        Check VM disk's order on pci/ccw bus.

        :param targets_name. Disks target list.
        :return: True if check successfully.
        """
        logging.info("Checking VM disks order...")
        xml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        disk_list = xml.devices.by_device_tag("disk")
        slot_dict = {}
        # Get the disks order attribute's value.
        for disk in disk_list:
            if 'virtio' == disk.target['bus']:
                selector = 'devno' if disk.address.attrs['type'] == 'ccw' else 'slot'
                slot_dict[disk.target['dev']] = int(
                    disk.address.attrs[selector], base=16)
        # Disk's order should be the same with disk target name.
        s_dev = sorted(list(slot_dict.keys()))
        s_slot = sorted(list(slot_dict.values()))
        for i in range(len(s_dev)):
            if s_dev[i] in targets_name and slot_dict[s_dev[i]] != s_slot[i]:
                return False
        return True

    def setup_nfs_disk(disk_name, disk_type, disk_format="raw", is_disk=True):
        """
        Setup nfs disk.

        :param disk_name: specified disk name.
        :param disk_type: specified disk type
        :param disk_format: specified disk format
        :param is_disk: specified return value type
        :return: Disk if is_disk is True, otherwise return mount_dir.
        """
        nfs_tmp_folder = data_dir.get_data_dir()
        mount_src = os.path.join(nfs_tmp_folder, "nfs-export")
        if not os.path.exists(mount_src):
            os.mkdir(mount_src)
        mount_dir = os.path.join(nfs_tmp_folder, "nfs-mount")

        if disk_type in ["file", "floppy", "iso"]:
            disk_path = "%s/%s" % (mount_src, disk_name)
            device_source = libvirt.create_local_disk(disk_type, disk_path, "2",
                                                      disk_format=disk_format)
            os.chmod(device_source, 0o777)
            #Format the disk.
            if disk_type in ["file", "floppy"]:
                cmd = ("mkfs.ext3 -F %s && setsebool virt_use_nfs true"
                       % device_source)
                if process.system(cmd, ignore_status=True, shell=True):
                    test.cancel("Format disk failed")

        nfs_params = {"nfs_mount_dir": mount_dir, "nfs_mount_options": "rw",
                      "nfs_mount_src": mount_src, "setup_local_nfs": "yes",
                      "export_options": "rw,no_root_squash"}

        nfs_obj = nfs.Nfs(nfs_params)
        nfs_obj.setup()
        if not nfs_obj.mount():
            return None

        disk = {"disk_dev": nfs_obj, "format": "nfs", "source":
                "%s/%s" % (mount_dir, os.path.split(device_source)[-1])}
        if is_disk:
            return disk
        else:
            return mount_dir

    def download_iso(iso_url, target_iso="/var/lib/libvirt/images/boot.iso"):
        """
        Download given iso file url.

        :param iso_url: downloaded iso url.
        :param targetiso: target iso path
        :return: downloaded target iso path if succeed, otherwise test fail.
        """
        if utils_package.package_install("wget"):
            def _download():
                download_cmd = ("wget %s -O %s" % (iso_url, target_iso))
                if process.system(download_cmd, verbose=False, shell=True):
                    test.error("Failed to download iso file")
                return True
            utils_misc.wait_for(_download, timeout=300)
            return target_iso
        else:
            test.fail("Fail to install wget")

    def prepare_disk(path, disk_format, disk_device, disk_device_type):
        """
        Prepare the disk for a given disk format.
        """
        disk = {}
        # Check if we test with a non-existed disk.
        if os.path.split(path)[-1].startswith("notexist."):
            disk.update({"format": disk_format,
                         "source": path})

        elif disk_format == "scsi":
            scsi_option = params.get("virt_disk_device_scsi_option", "")
            disk_source = libvirt.create_scsi_disk(scsi_option)
            if disk_source:
                disk.update({"format": "scsi",
                             "source": disk_source})
            else:
                test.cancel("Get scsi disk failed")

        elif disk_format in ["iso", "floppy"]:
            if boot_iso_url:
                disk_path = download_iso(boot_iso_url)
            else:
                disk_path = libvirt.create_local_disk(disk_format, path)
            disk.update({"format": disk_format,
                         "source": disk_path})
        elif disk_format == "nfs":
            if pool_type and pool_type == "netfs":
                vol_name, vol_path = create_volume(pvt)
                if disk_device_type == "volume":
                    disk.update({"format": disk_format,
                                 "source": {"attrs": {'pool': pool_name,
                                                      'volume': vol_name,
                                                      'mode': "host"}}})
                else:
                    disk.update({"format": disk_format,
                                 "source": vol_path})
                logging.debug("disk source is:%s", disk['source'])
            else:
                nfs_disk_type = params.get("nfs_disk_type", None)
                disk.update(setup_nfs_disk(os.path.split(path)[-1], nfs_disk_type))

        elif disk_format == "iscsi":
            # Create iscsi device if needed.
            image_size = params.get("image_size", "2G")
            if disk_device_type == "volume":
                vol_name, vol_path = create_volume(pvt)
                logging.debug("create volume:%s", vol_name)
                mode = "host"
                if params.get("pool_source_mode", None):
                    mode = params.get("pool_source_mode")
                disk.update({"format": disk_format,
                             "source": {"attrs": {'pool': pool_name,
                                                  'volume': vol_name,
                                                  'mode': mode}}})
                logging.debug("disk source is:%s", disk['source'])
            elif disk_device_type == "network":
                if auth_usage:
                    global secret_uuid, iscsi_target, lun_num
                    secret_uuid = create_auth_secret()
                    # Setup iscsi target
                    try:
                        iscsi_target, lun_num = libvirt.setup_or_cleanup_iscsi(
                            is_setup=True, is_login=False, image_size=image_size,
                            chap_user=chap_user, chap_passwd=chap_passwd)
                    except Exception as iscsi_ex:
                        logging.debug("Failed to create iscsi lun: %s", str(iscsi_ex))
                        libvirt.setup_or_cleanup_iscsi(is_setup=False)
                    disk.update({"format": disk_format,
                                 "source": {"attrs": {"protocol": "iscsi",
                                                      "name": "%s/%s" % (iscsi_target, lun_num)},
                                            "hosts": [{"name": '127.0.0.1', "port": '3260'}]}})
                    logging.debug("disk source is:%s", disk['source'])
            else:
                device_source = libvirt.setup_or_cleanup_iscsi(
                    is_setup=True, is_login=True, image_size=image_size,
                    chap_user=chap_user, chap_passwd=chap_passwd)
                device_source_backup = device_source
                logging.debug("iscsi dev name: %s", device_source)

                # Format the disk and make file system.
                libvirt.mk_label(device_source)
                libvirt.mk_part(device_source)
                # Run partprobe to make the change take effect.
                process.run("partprobe", ignore_status=True, shell=True)
                libvirt.mkfs("%s1" % device_source, "ext3")
                if disk_device == "lun" and disk_device_type == "block":
                    device_source = device_source_backup
                else:
                    device_source += "1"
                disk.update({"format": disk_format,
                             "source": device_source})
        elif disk_format == "lvm":
            image_size = params.get("image_size", "2G")
            device_source = libvirt.setup_or_cleanup_iscsi(
                is_setup=True, is_login=True, image_size=image_size)
            logging.debug("iscsi dev name: %s", device_source)
            lv_utils.vg_create(vg_name, device_source)
            device_source = libvirt.create_local_disk("lvm",
                                                      size="10M",
                                                      vgname=vg_name,
                                                      lvname=lv_name)
            logging.debug("New created volume: %s", lv_name)
            disk.update({"format": 'lvm',
                         "source": device_source})
        elif disk_format in ["raw", "qcow2", "vhdx", "qed"]:
            if network_iscsi_baseimg:
                if auth_usage:
                    secret_uuid = create_auth_secret()
                # Setup iscsi target
                image_size = params.get("image_size", "2G")
                try:
                    iscsi_target, lun_num = libvirt.setup_or_cleanup_iscsi(
                        is_setup=True, is_login=False, image_size=image_size,
                        chap_user=chap_user, chap_passwd=chap_passwd)
                except Exception as iscsi_ex:
                    logging.debug("Failed to create iscsi lun: %s", str(iscsi_ex))
                    libvirt.setup_or_cleanup_iscsi(is_setup=False)
                json_str = ('json:{"driver":"raw", "file":{"lun":"%s",'
                            '"portal":"127.0.0.1","driver":"iscsi", "transport":"tcp",'
                            '"target":"%s", "user":"%s", "password-secret":"sec"}}'
                            % (lun_num, iscsi_target, chap_user))
                cmd = ("qemu-img create --object secret,data='%s',id=sec,format=raw "
                       "-f qcow2 -b '%s' -o backing_fmt=raw %s"
                       % (chap_passwd, json_str, path))
                ret = process.run(cmd, shell=True)
                libvirt.check_exit_status(ret)
                disk.update({"format": disk_format,
                             "source": path})
            else:
                disk_size = params.get("virt_disk_device_size", "1")
                device_source = libvirt.create_local_disk(
                    "file", path, disk_size, disk_format=disk_format)
                disk.update({"format": disk_format,
                             "source": device_source})
            if file_mount_point_type:
                for cmd in ("touch %s" % tmp_demo_img, "mount --bind %s %s" % (path, tmp_demo_img)):
                    try:
                        cmd_result = process.run(cmd, shell=True)
                        cmd_result.stdout = cmd_result.stdout_text
                        cmd_result.stderr = cmd_result.stderr_text
                    except Exception as cmdError:
                        os.remove(tmp_demo_img)
        return disk

    def check_disk_format(targets_name, targets_format):
        """
        Check VM disk's type.

        :param targets_name. Device name list.
        :param targets_format. Device format list.
        :return: True if check successfully.
        """
        logging.info("Checking VM disks type... ")
        for tn, tf in zip(targets_name, targets_format):
            disk_format = vm_xml.VMXML.get_disk_attr(vm_name, tn,
                                                     "driver", "type")
            if disk_format not in [None, tf]:
                return False
        return True

    def check_vm_partitions(devices, targets_name, exists=True):
        """
        Check VM disk's partition.

        :return: True if check successfully.
        """
        logging.info("Checking VM partittion...")
        try:
            session = vm.wait_for_login()
            # Here the script needs wait for a while for the guest to
            # recognize the hotplugged disk on PPC
            add_sleep()
            for i in list(range(len(devices))):
                if devices[i] == "cdrom":
                    s, o = session.cmd_status_output(
                        "ls /dev/sr0 && mount /dev/sr0 /mnt &&"
                        " ls /mnt && umount /mnt")
                    logging.info("cdrom devices in VM:\n%s", o)
                elif devices[i] == "floppy":
                    s, o = session.cmd_status_output(
                        "modprobe floppy && ls /dev/fd0")
                    logging.info("floppy devices in VM:\n%s", o)
                else:
                    if targets_name[i] == "hda":
                        target = "sda"
                    else:
                        target = targets_name[i]
                    status, result = session.cmd_status_output(
                        "cat /proc/partitions")
                    s, o = session.cmd_status_output(
                        "grep %s /proc/partitions" % target)
                    logging.info("Disk devices in VM:\n%s", o)
                if s != 0:
                    if exists:
                        session.close()
                        return False
                else:
                    if not exists:
                        session.close()
                        return False
            session.close()
            return True
        except (remote.LoginError, virt_vm.VMError, aexpect.ShellError) as e:
            logging.error(str(e))
            return False

    def check_vm_block_size(targets_name, log_size, phy_size):
        """
        Check VM disk's blocksize.

        :param logical_size. Device logical block size.
        :param physical_size. Device physical block size.
        :return: True if check successfully.
        """
        logging.info("Checking VM block size...")
        try:
            session = vm.wait_for_login()
            # Here the script needs wait for a while for the guest to
            # recognize the block on PPC
            add_sleep()
            for target in targets_name:
                cmd = "cat /sys/block/%s/queue/" % target
                s, o = session.cmd_status_output("%slogical_block_size"
                                                 % cmd)
                logging.debug("logical block size in VM:\n%s", o)
                if s != 0 or o.strip() != log_size:
                    session.close()
                    return False
                s, o = session.cmd_status_output("%sphysical_block_size"
                                                 % cmd)
                logging.debug("physical block size in VM:\n%s", o)
                if s != 0 or o.strip() != phy_size:
                    session.close()
                    return False
            session.close()
            return True
        except (remote.LoginError, virt_vm.VMError, aexpect.ShellError) as e:
            logging.error(str(e))
            return False

    def check_vm_discard(target_name):
        """
        Check VM discard value.

        :param target_name. Device target name.
        """
        logging.info("Checking VM discard...")
        try:
            session = vm.wait_for_login()
            cmd = ("fdisk -l  /dev/{0} && mkfs.ext4 -F /dev/{0} && "
                   "mkdir -p test && mount /dev/{0} test && "
                   "dd if=/dev/zero of=test/file bs=1M count=300 && sync"
                   .format(target_name))
            status, output = session.cmd_status_output(cmd)
            if status != 0:
                session.close()
                test.fail("Failed due to: %s" % output.strip())
            discard_cmd = "cat /sys/bus/pseudo/drivers/scsi_debug/map"
            cmd_result = process.run(discard_cmd, shell=True).stdout_text.strip()
            # Get discard map list.
            discard_map_list_before = len(cmd_result.split(','))

            cmd = ("rm -rf file && sync && "
                   "fstrim test")
            status, output = session.cmd_status_output(cmd)
            if status != 0:
                session.close()
                test.fail("Failed due to: %s", output)
            session.close()
            cmd_result = process.run(discard_cmd, shell=True).stdout_text.strip()
            discard_map_list_after = len(cmd_result.split(','))
            # After file is deleted,discard map number should be shorter.
            if discard_map_list_after >= discard_map_list_before:
                test.fail("discard map number doesn't reduce after file is deleted.")
        except (remote.LoginError, virt_vm.VMError, aexpect.ShellError) as e:
            logging.error(str(e))
            test.error("Check Vm discard failed")

    def check_vm_pci_bridge():
        """
        Check pci bridge in VM internal.
        """
        logging.debug("Checking VM pci bridge")
        session = vm.wait_for_login()
        status, output = session.cmd_status_output("lspci|grep 'PCI bridge'")
        logging.debug("lspci information in VM: %s", output)
        if status != 0:
            session.close()
            test.fail("Failed to check VM pci bridge")
        session.close()

    def check_readonly(targets_name):
        """
        Check disk readonly option.
        """
        logging.info("Checking disk readonly option...")
        try:
            session = vm.wait_for_login()
            for target in targets_name:
                target_list = ["hdc"]
                if arch == 's390x':
                    target_list = ["hdc", "sda"]
                if target in target_list:
                    mount_cmd = "mount /dev/cdrom /mnt"
                elif target == "fda":
                    mount_cmd = "modprobe floppy && mount /dev/fd0 /mnt"
                else:
                    mount_cmd = "mount /dev/%s /mnt" % target
                cmd = ("(%s && ls /mnt || exit 1) && (echo "
                       "'test' > /mnt/test || umount /mnt)" % mount_cmd)
                s, o = session.cmd_status_output(cmd)
                logging.debug("cmd exit: %s, output: %s", s, o)
                if s or "Read-only file system" not in o:
                    session.close()
                    return False
            session.close()
            return True
        except (remote.LoginError, virt_vm.VMError, aexpect.ShellError) as e:
            logging.error(str(e))
            return False

    def check_bootorder_snapshot(disk_name):
        """
        Check VM disk's bootorder option with snapshot.

        :param disk_name: the target disk to be checked.
        """
        logging.info("Checking diskorder option with snapshot...")
        snapshot1 = "s1"
        snapshot2 = "s2"
        snapshot2_file = os.path.join(data_dir.get_data_dir(), "s2")
        ret = virsh.snapshot_create(vm_name, "", **virsh_dargs)
        libvirt.check_exit_status(ret)

        ret = virsh.snapshot_create_as(vm_name, "%s --disk-only" % snapshot1,
                                       **virsh_dargs)
        libvirt.check_exit_status(ret)

        ret = virsh.snapshot_dumpxml(vm_name, snapshot1)
        libvirt.check_exit_status(ret)

        cmd = "echo \"%s\" | grep %s.%s" % (ret.stdout.strip(), disk_name, snapshot1)
        if process.system(cmd, ignore_status=True, shell=True):
            test.cancel("Check snapshot disk failed")

        ret = virsh.snapshot_create_as(vm_name,
                                       "%s --memspec file=%s,snapshot=external"
                                       % (snapshot2, snapshot2_file),
                                       **virsh_dargs)
        libvirt.check_exit_status(ret)

        ret = virsh.dumpxml(vm_name)
        libvirt.check_exit_status(ret)

        cmd = ("echo \"%s\" | grep -A 16 %s.%s | grep \"boot order='%s'\""
               % (ret.stdout.strip(), disk_name, snapshot2, bootorder))
        if process.system(cmd, ignore_status=True, shell=True):
            test.error("Check snapshot disk with bootorder failed")

        snap_lists = virsh.snapshot_list(vm_name)
        if snapshot1 not in snap_lists or snapshot2 not in snap_lists:
            test.error("Check snapshot list failed")

        # Check virsh save command after snapshot.
        save_file = "/tmp/%s.save" % vm_name
        ret = virsh.save(vm_name, save_file, **virsh_dargs)
        libvirt.check_exit_status(ret)

        # Check virsh restore command after snapshot.
        ret = virsh.restore(save_file, **virsh_dargs)
        libvirt.check_exit_status(ret)

        #Passed all test.
        os.remove(save_file)

    def check_transient_disk_keyword():
        """
        Check VM disk with TRANSIENT keyword.

        """
        logging.info("Checking disk with transient keyword...")

        ret = virsh.dumpxml(vm_name, ignore_status=False)

        cmd = ("echo \"%s\" | grep '<source file=.*TRANSIENT.*/>'" % ret.stdout_text)
        if process.system(cmd, ignore_status=False, shell=True):
            test.fail("Check transident disk failed")

    def check_restart_transient_vm(target_name):
        """
        Check VM transient feature.

        :param target_name. Device target name.
        """
        logging.info("Checking VM transident...")
        try:
            session = vm.wait_for_login()
            cmd = ("fdisk -l  /dev/{0} && mkfs.ext4 -F /dev/{0} && "
                   "mkdir -p /test && mount /dev/{0} /test && "
                   "dd if=/dev/zero of=/test/transient.txt bs=1M count=300 && sync"
                   .format(target_name))
            status, output = session.cmd_status_output(cmd)
            if status != 0:
                session.close()
                test.fail("Failed due to: %s" % output.strip())
            session.close()
            # Destroy VM.
            if vm.is_alive():
                vm.destroy(gracefully=False)
            vm.start()
            session = vm.wait_for_login()

            cmd = ("mkdir -p /test && mount /dev/{0} /test &&  ls -l /test/ && ls -l /test/transient.txt"
                   .format(target_name))
            status, output = session.cmd_status_output(cmd)
            logging.info("check /test/transient.txt file output in VM: %s", output.strip())
            if status == 0:
                session.close()
                test.fail("Still find file in transient disk after VM restart due to: %s" % output.strip())
            session.close()
        except (remote.LoginError, virt_vm.VMError, aexpect.ShellError) as e:
            logging.error(str(e))
            test.error("Check Vm disk transient feature failed")

    def check_boot_console(bootorders):
        """
        Get console output and check bootorder.
        """
        # Get console output.
        vm.serial_console.read_until_output_matches(
            ["Hard Disk"], utils_misc.strip_console_codes)
        output = vm.serial_console.get_stripped_output()
        logging.debug("serial output: %s", output)
        lines = re.findall(r"^Booting from (.+)...", output, re.M)
        logging.debug("lines: %s", lines)
        if len(lines) != len(bootorders):
            return False
        for i in list(range(len(bootorders))):
            if lines[i] != bootorders[i]:
                return False

        return True

    def check_boot_output():
        """
        Check console output.
        """
        # Get console output.
        vm.serial_console.read_until_output_matches(
            [r"Escape character is.*"], utils_misc.strip_console_codes)
        output = vm.serial_console.get_stripped_output()
        if 'Connected to domain' not in output:
            test.fail("boot serial console doesn't find expected keyword:Connected to domain")

    def check_disk_cache_mode(d_target, expect_disk_cache_mode):
        """
        Check VM disk's cache mode.

        :param d_target: the target name of disk.
        :param: expect_disk_cache_mode: the expected disk cache mode.
        """
        cache = vm_xml.VMXML.get_disk_attr(vm_name, d_target, "driver", "cache")
        if not cache:
            test.error("Can not get disk cache mode value")
        if cache != expect_disk_cache_mode:
            test.fail("Disk cache mode:%s is not expected:%s " % (cache, expect_disk_cache_mode))

    def check_disk_save_restore(save_file, device_targets,
                                startup_policy):
        """
        Check domain save and restore operation.
        """
        # Save the domain.
        ret = virsh.save(vm_name, save_file,
                         **virsh_dargs)
        libvirt.check_exit_status(ret)

        # Restore the domain.
        restore_error = False
        # Check disk startup policy option
        if "optional" in startup_policy:
            os.remove(disks[0]["source"])
            restore_error = True
        ret = virsh.restore(save_file, **virsh_dargs)
        libvirt.check_exit_status(ret, restore_error)
        if restore_error:
            return

        # Connect to the domain and check disk.
        try:
            session = vm.wait_for_login()
            cmd = ("ls /dev/%s && mkfs.ext3 -F /dev/%s && mount /dev/%s"
                   " /mnt && ls /mnt && touch /mnt/test && umount /mnt"
                   % (device_targets[0], device_targets[0], device_targets[0]))
            status, output = session.cmd_status_output(cmd)
            if status:
                session.close()
                test.error("Failed to read/write disk in VM:"
                           " %s" % output)
            session.close()
        except (remote.LoginError, virt_vm.VMError, aexpect.ShellError) as detail:
            test.error(str(detail))

    def check_dom_iothread():
        """
        Check iothread by qemu-monitor-command.
        """
        ret = virsh.qemu_monitor_command(vm_name,
                                         '{"execute": "query-iothreads"}',
                                         "--pretty")
        libvirt.check_exit_status(ret)
        logging.debug("Domain iothreads: %s", ret.stdout.strip())
        iothreads_ret = json.loads(ret.stdout.strip())
        if len(iothreads_ret['return']) != int(dom_iothreads):
            test.fail("Failed to check domain iothreads")

    def get_device_addr(device_type, elem_type):
        """Get the address of testing input or hub from VM XML as a dict."""
        cur_vm_xml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        addr = None
        for elem in cur_vm_xml.xmltreefile.findall('/devices/%s' % device_type):
            if (elem.get('type') == elem_type):
                addr_elem = elem.find('./address')
                if addr_elem is not None:
                    addr = Address.new_from_element(addr_elem).attrs
                    break
        return addr

    def create_addtional_disk(disk_type, disk_path, disk_format, disk_device_type,
                              disk_device, disk_target, disk_bus):
        """
        Create another disk for a given path,customize some attributes.
        :param disk_type: the type of disk.
        :param disk_path: the path of disk.
        :param disk_format: the format to disk image.
        :param disk_device_type: the disk device type.
        :param disk_device: the device of disk.
        :param disk_target: the target of disk.
        :param disk_bus: the target bus of disk.
        :return: disk object if created successfully.
        """

        disk_source = libvirt.create_local_disk(disk_type, disk_path, '10', disk_format)
        disks_img.append({"format": disk_format,
                          "source": disk_path, "path": disk_path})
        custom_disk = Disk(type_name=disk_device_type)
        custom_disk.device = disk_device
        source_dict = {'file': disk_source}
        custom_disk.source = custom_disk.new_disk_source(
            **{"attrs": source_dict})
        target_dict = {"dev": disk_target, "bus": disk_bus}
        custom_disk.target = target_dict
        driver_dict = {"name": "qemu", 'type': disk_format}
        custom_disk.driver = driver_dict
        return custom_disk

    def clean_up_lvm():
        """Clean up lvm. """
        libvirt.delete_local_disk("lvm", vgname=vg_name, lvname=lv_name)
        lv_utils.vg_remove(vg_name)
        process.system("pvremove %s" % device_source, ignore_status=True, shell=True)
        process.system("rm -rf /dev/%s" % vg_name, ignore_status=True, shell=True)
        libvirt.setup_or_cleanup_iscsi(False)

    def virt_xml_validate(xml_file, validate_error=False):
        """Validate xml file. """
        exit_status = process.system("/bin/virt-xml-validate %s" % xml_file, ignore_status=True, shell=True)
        if not validate_error:
            if exit_status != 0:
                test.fail("Run command expect succeed,but failed")
            else:
                logging.debug("Run command succeed")
        elif validate_error and exit_status == 0:
            test.fail("Run command expect fail, but run "
                      "successfully.")

    def create_auth_secret():
        """Create auth secret."""
        sec_xml = secret_xml.SecretXML("no", "yes")
        sec_xml.description = "iSCSI secret"
        sec_xml.auth_type = auth_type
        sec_xml.auth_username = chap_user
        sec_xml.usage = secret_usage_type
        sec_xml.target = secret_usage_target
        sec_xml.xmltreefile.write()

        ret = virsh.secret_define(sec_xml.xml)
        libvirt.check_exit_status(ret)

        secet_uuid_value = re.findall(r".+\S+(\ +\S+)\ +.+\S+",
                                      ret.stdout.strip())[0].lstrip()
        logging.debug("Secret uuid %s", secet_uuid_value)
        if not secet_uuid_value:
            test.error("Failed to get secret uuid")

        # Set secret value
        encoding = locale.getpreferredencoding()
        secret_string = base64.b64encode(chap_passwd.encode(encoding)).decode(encoding)
        ret = virsh.secret_set_value(secet_uuid_value, secret_string,
                                     **virsh_dargs)
        libvirt.check_exit_status(ret)
        return secet_uuid_value

    def create_iscsi_pool():
        """
        Setup iSCSI target,and create one iSCSI pool.
        """
        libvirt.setup_or_cleanup_iscsi(is_setup=False)
        iscsi_target, lun_num = libvirt.setup_or_cleanup_iscsi(is_setup=True,
                                                               is_login=False,
                                                               image_size='1G',
                                                               chap_user="",
                                                               chap_passwd="",
                                                               portal_ip=disk_src_host)
        # Define an iSCSI pool xml to create it
        pool_src_xml = pool_xml.SourceXML()
        pool_src_xml.host_name = pool_src_host
        pool_src_xml.device_path = iscsi_target
        poolxml = pool_xml.PoolXML(pool_type=pool_type)
        poolxml.name = pool_name
        poolxml.set_source(pool_src_xml)
        poolxml.target_path = "/dev/disk/by-path"

        # Create iSCSI pool.
        pool_result = virsh.pool_state_dict()
        if pool_name and pool_name in pool_result:
            virsh.pool_destroy(pool_name, **virsh_dargs)
        cmd_result = virsh.pool_create(poolxml.xml, **virsh_dargs)
        libvirt.check_exit_status(cmd_result)

    def create_nfs_pool():
        """
        Setup nfs pool.
        """
        # Create nfs pool.
        pool_result = virsh.pool_state_dict()
        if pool_name and pool_name in pool_result:
            virsh.pool_destroy(pool_name, **virsh_dargs)
        pool_target = "admin"
        pvt.pre_pool(pool_name, pool_type, pool_target, emulated_image, image_size="40M")

    def create_volume(pvt):
        """
        Create iSCSI volume.

        :params pvt: PoolVolumeTest object
        """
        try:
            if pool_type == "iscsi":
                create_iscsi_pool()
            elif pool_type == "netfs":
                create_nfs_pool()
                pvt.pre_vol(vol_name=vol_name, vol_format=vol_format,
                            capacity=capacity, allocation=None,
                            pool_name=pool_name)
        except Exception as pool_exception:
            pvt.cleanup_pool(pool_name, pool_type, pool_target,
                             emulated_image, **virsh_dargs)
            test.error("Error occurred when prepare pool xml with message:%s\n",
                       str(pool_exception))

        def get_vol():
            """Get the volume info"""
            # Refresh the pool
            cmd_result = virsh.pool_refresh(pool_name)
            libvirt.check_exit_status(cmd_result)
            # Get volume name
            cmd_result = virsh.vol_list(pool_name, **virsh_dargs)
            libvirt.check_exit_status(cmd_result)
            vol_list = []
            vol_list = re.findall(r"(\S+)\ +(\S+)",
                                  str(cmd_result.stdout.strip()))
            if len(vol_list) > 1:
                return vol_list[1]
            else:
                return None

        # Wait for a while so that we can get the volume info
        vol_info = utils_misc.wait_for(get_vol, 10)
        if vol_info:
            tmp_vol_name, tmp_vol_path = vol_info
        else:
            test.error("Failed to get volume info")
        process.run('qemu-img create -f qcow2 %s %s' % (tmp_vol_path, '100M'),
                    shell=True)
        return vol_info

    def config_libvirtd_log():
        """ configure libvirtd log level"""
        log_outputs = "1:file:%s" % log_config_path
        libvirtd_config.log_outputs = log_outputs
        libvirtd_config.log_filters = "1:json 1:libvirt 1:qemu 1:monitor 3:remote 4:event"
        utils_libvirtd.libvirtd_restart()

    def check_info_in_libvird_log_file(matchedMsg=None):
        """
        Check if information can be found in libvirtd log.

        :params matchedMsg: expected matched messages
        """
        # Check libvirtd log file.
        libvirtd_log_file = log_config_path
        if not os.path.exists(libvirtd_log_file):
            test.fail("Expected VM log file: %s not exists" % libvirtd_log_file)
        cmd = ("grep -nr '%s' %s" % (matchedMsg, libvirtd_log_file))
        return process.run(cmd, ignore_status=True, shell=True).exit_status == 0

    status_error = "yes" == params.get("status_error", "no")
    define_error = "yes" == params.get("define_error", "no")
    validate_error = "yes" == params.get("validate_error", "no")
    dom_iothreads = params.get("dom_iothreads")

    # Disk specific attributes.
    devices = params.get("virt_disk_device", "disk").split()
    device_source_names = params.get("virt_disk_device_source").split()
    device_targets = params.get("virt_disk_device_target", "vda").split()
    device_formats = params.get("virt_disk_device_format", "raw").split()
    device_types = params.get("virt_disk_device_type", "file").split()
    # After block-dev introduced, 'host_device' driver expects either a character or block device
    if libvirt_version.version_compare(6, 0, 0) and params.get("virt_disk_device_type") == "block block block block":
        device_types = "file file file file".split()
    device_bus = params.get("virt_disk_device_bus", "virtio").split()
    driver_options = params.get("driver_option", "").split()
    device_bootorder = params.get("virt_disk_boot_order", "").split()
    device_readonly = params.get("virt_disk_option_readonly",
                                 "no").split()
    device_attach_error = params.get("disks_attach_error", "").split()
    device_attach_option = params.get("disks_attach_option", "").split(';')
    device_address = params.get("virt_disk_addr_options", "").split()
    startup_policy = params.get("virt_disk_device_startuppolicy", "").split()
    bootorder = params.get("disk_bootorder", "")
    bootdisk_target = params.get("virt_disk_bootdisk_target", "vda")
    bootdisk_bus = params.get("virt_disk_bootdisk_bus", "virtio")
    bootdisk_driver = params.get("virt_disk_bootdisk_driver", "")
    serial = params.get("virt_disk_serial", "").split()
    wwn = params.get("virt_disk_wwn", "").split()
    vendor = params.get("virt_disk_vendor", "")
    product = params.get("virt_disk_product", "")
    add_disk_driver = params.get("add_disk_driver")
    iface_driver = params.get("iface_driver_option", "")
    bootdisk_snapshot = params.get("bootdisk_snapshot", "")
    snapshot_option = params.get("snapshot_option", "")
    snapshot_error = "yes" == params.get("snapshot_error", "no")
    add_usb_device = "yes" == params.get("add_usb_device", "no")
    duplicate_target = params.get("virt_disk_duplicate_target", "no")
    hotplug = "yes" == params.get(
        "virt_disk_device_hotplug", "no")
    device_at_dt_disk = "yes" == params.get("virt_disk_at_dt_disk", "no")
    device_cold_dt = "yes" == params.get("virt_disk_cold_dt", "no")
    device_with_source = "yes" == params.get(
        "virt_disk_with_source", "yes")
    virtio_scsi_controller = "yes" == params.get(
        "virtio_scsi_controller", "no")
    virt_disk_with_duplicate_scsi_controller_index = "yes" == params.get(
        "virt_disk_with_duplicate_scsi_controller_index", "no")
    multi_ide_controller = "yes" == params.get(
        "disk_virtio_multi_ide_controller", "no")
    virtio_scsi_controller_driver = params.get(
        "virtio_scsi_controller_driver", "")
    virtio_scsi_controller_addr = params.get(
        "virtio_scsi_controller_addr", "")
    source_path = "yes" == params.get(
        "virt_disk_device_source_path", "yes")
    check_partitions = "yes" == params.get(
        "virt_disk_check_partitions", "yes")
    check_discard = "yes" == params.get(
        "virt_disk_check_discard", "no")
    check_pci_bridge = "yes" == params.get(
        "virt_disk_check_pci_bridge", "no")
    check_partitions_hotunplug = "yes" == params.get(
        "virt_disk_check_partitions_hotunplug", "yes")
    test_slots_order = "yes" == params.get(
        "virt_disk_device_test_order", "no")
    # allow_disk_format_probing configuration was removed since libvirt-4.5
    test_disks_format = False if libvirt_version.version_compare(4, 5, 0) \
        else "yes" == params.get("virt_disk_device_test_format", "no")
    test_block_size = "yes" == params.get(
        "virt_disk_device_test_block_size", "no")
    test_file_img_on_disk = "yes" == params.get(
        "test_file_image_on_disk", "no")
    test_with_boot_disk = "yes" == params.get(
        "virt_disk_with_boot_disk", "no")
    test_disk_option_cmd = "yes" == params.get(
        "test_disk_option_cmd", "no")
    test_disk_type_dir = "yes" == params.get(
        "virt_disk_test_type_dir", "no")
    test_disk_bootorder = "yes" == params.get(
        "virt_disk_test_bootorder", "no")
    test_disk_bootorder_snapshot = "yes" == params.get(
        "virt_disk_test_bootorder_snapshot", "no")
    test_boot_console = "yes" == params.get(
        "virt_disk_device_boot_console", "no")
    test_disk_readonly = "yes" == params.get(
        "virt_disk_device_test_readonly", "no")
    test_disk_snapshot = "yes" == params.get(
        "virt_disk_test_snapshot", "no")
    test_disk_save_restore = "yes" == params.get(
        "virt_disk_test_save_restore", "no")
    test_bus_device_option = "yes" == params.get(
        "test_bus_option_cmd", "no")
    snapshot_before_start = "yes" == params.get(
        "snapshot_before_start", "no")
    disk_cdrom_update_boot_order = "yes" == params.get(
        "disk_cdrom_update_boot_order", "no")
    disk_floppy_update_boot_order = "yes" == params.get(
        "disk_floppy_update_boot_order", "no")
    vg_name = params.get("virt_disk_vg_name", "vg_test_0")
    lv_name = params.get("virt_disk_lv_name", "lv_test_0")
    disk_transient = "yes" == params.get("disk_transient", "no")
    virt_xml_validate_test = "yes" == params.get("virt_xml_validate_test", "no")
    test_attach_device_iteration = "yes" == params.get("test_attach_device_iteration", "no")
    attach_device_as_scsi_lun = "yes" == params.get("attach_device_as_scsi_lun", "no")
    attach_ccw_address_at_dt_disk = "yes" == params.get("disk_attach_ccw_address_at_dt_disk", "no")

    # Chap auth parameters.
    chap_user = params.get("iscsi_user", "")
    chap_passwd = params.get("iscsi_password", "")
    auth_usage = "yes" == params.get("auth_usage", "")
    auth_type = params.get("auth_type")
    secret_usage_target = params.get("secret_usage_target")
    secret_usage_type = params.get("secret_usage_type")

    # backing Store parameters
    network_iscsi_baseimg = "yes" == params.get("network_iscsi_baseimg", "no")
    bs_device_types = params.get("virt_disk_device_type_bs", "file").split()
    bs_device_formats = params.get("virt_disk_device_format_bs", "qcow2").split()

    # Storage pool and disk related parameters.S
    pool_name = params.get("pool_name", "iscsi_pool")
    pool_type = params.get("pool_type")
    pool_target = params.get("pool_target", "/dev/disk/by-path")
    pool_src_host = params.get("pool_source_host", "127.0.0.1")
    disk_src_host = params.get("disk_source_host", "127.0.0.1")
    emulated_image = params.get("emulated_image", "emulated-iscsi")
    vol_name = params.get("volume_name")
    capacity = params.get("volume_size", "4096")
    vol_format = params.get("volume_format")
    file_mount_point_type = "yes" == params.get("file_mount_point_type", "no")

    # Check disk cache mode.
    check_disk_cache = "yes" == params.get("check_disk_cache_mode", "no")
    disk_cache_mode = params.get("disk_cache_mode")

    # Minimal VM xml,special case.
    test_minimal_xml = "yes" == params.get("test_minimal_xml", "no")

    # Check special characters xml.
    test_special_characters_xml = "yes" == params.get("test_special_characters_xml", "no")

    # Cancel early
    if (dom_iothreads and not
            libvirt_version.version_compare(1, 2, 8)):
        test.cancel("iothreads not supported for"
                    " this libvirt version")

    if (attach_ccw_address_at_dt_disk and
            device_attach_option and
            "ccw:00" in device_attach_option[0] and
            utils_misc.compare_qemu_version(2, 12, 0, is_rhev=False)):
        test.cancel("ccsid values are unrestricted in this"
                    " qemu version")

    # Backup selinux_mode and virt_use_nfs status
    virt_use_nfs_off = "yes" == params.get("virt_use_nfs_off", "no")
    if virt_use_nfs_off:
        selinux_mode = utils_selinux.get_status()
        logging.info("Enable virt NFS SELinux boolean")
        result = process.run("getsebool virt_use_nfs", shell=True, ignore_status=True)
        if result.exit_status:
            test.fail("Failed to get virt_use_nfs value")
        backup_virt_use_nfs_status = result.stdout_text.strip().split("-->")[1].strip()
        logging.debug("debug virt_use:%s", backup_virt_use_nfs_status)

    virt_disk_with_boot_nfs_pool = "yes" == params.get("virt_disk_with_boot_nfs_pool", "no")
    iso_url = ("https://dl.fedoraproject.org/pub/fedora/linux/releases",
               "/30/Everything/x86_64/os/images/boot.iso")
    default_iso_url = ''.join(iso_url)
    boot_iso_url = params.get("boot_iso_url")
    if virt_disk_with_boot_nfs_pool and 'EXAMPLE_BOOT_ISO_URL' in boot_iso_url:
        boot_iso_url = default_iso_url

    # Restart libvirtd.
    restart_libvird = "yes" == params.get("restart_libvird", "no")
    virtio_disk_hot_unplug_event_watch = "yes" == params.get("virtio_disk_hot_unplug_event_watch", "no")

    # Configure libvirtd log level and path.
    log_file = params.get("log_file", "libvirtd.log")
    log_config_path = os.path.join(data_dir.get_data_dir(), log_file)
    libvirtd_config = LibvirtdConfig()

    if virtio_disk_hot_unplug_event_watch:
        config_libvirtd_log()

    if test_block_size:
        logical_block_size = params.get("logical_block_size")
        physical_block_size = params.get("physical_block_size")

    if any([test_boot_console, add_disk_driver]):
        if vm.is_dead():
            vm.start()
        session = vm.wait_for_login()
        if test_boot_console:
            # Setting console to kernel parameters
            vm.set_kernel_console("ttyS0", "115200",
                                  guest_arch_name=arch)
        if add_disk_driver:
            # Ignore errors here
            session.cmd("dracut --force --add-drivers '%s'"
                        % add_disk_driver, timeout=360)
        session.close()
        vm.shutdown()

    # Destroy VM.
    if vm.is_alive():
        vm.destroy(gracefully=False)

    # Back up xml file.
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    # Increase pci num to avoid error 'No more available PCI slots'
    if params.get("reset_pci_controllers_nums", "no") == "yes":
        libvirt_pcicontr.reset_pci_num(vm_name, 15)

    # For minimal VM xml,it need reconstruct one.
    if test_minimal_xml:
        minimal_vm_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        first_disk = vm.get_first_disk_devices()
        first_disk_source = first_disk['source']
        minimal_vm_xml_file = minimal_vm_xml.xml
        minimal_xml_content = """<domain type='kvm'>
        <name>%s</name>
        <memory unit='KiB'>1048576</memory>
        <currentMemory unit='KiB'>1048576</currentMemory>
        <vcpu placement='static'>1</vcpu>
        <os>
          <type arch='%s' machine='%s'>hvm</type>
          <boot dev='hd'/>
        </os>
        <devices>
          <emulator>/usr/libexec/qemu-kvm</emulator>
          <disk type='file' device='disk'>
            <driver name='qemu' type='qcow2'/>
            <source file='%s'/>
            <target dev='vda' bus='virtio'/>
          </disk>
        </devices>
        </domain>""" % (vm_name, arch, machine, first_disk_source)
        with open(minimal_vm_xml_file, 'w') as xml_file:
            xml_file.seek(0)
            xml_file.truncate()
            xml_file.write(minimal_xml_content)
        vm.undefine()
        if virsh.define(minimal_vm_xml_file).exit_status:
            test.cancel("can't create the domain")

    # For special characters VM xml,and disk image file name.
    if test_special_characters_xml:
        first_disk = vm.get_first_disk_devices()
        first_disk_source = first_disk['source']
        first_disk_source_rename = "%s.rhel7.4\.img**" % first_disk_source
        try:
            # Rename disk file image to another name with special characters.
            os.rename(first_disk_source, first_disk_source_rename)
            # Update disk source file.
            params.update({'disk_source_name': first_disk_source_rename,
                           'disk_type': 'file',
                           'disk_src_protocol': 'file'})
            libvirt.set_vm_disk(vm, params)
            vm.wait_for_login().close()
            if vm.is_alive():
                vm.destroy(gracefully=False)

            # Rename VM xml file to another name with special characters.
            special_vm_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
            special_vm_xml_file = special_vm_xml.xml
            special_vm_xml_file_rename = "%s.dump" % special_vm_xml_file
            vm.undefine()
            os.rename(special_vm_xml_file, special_vm_xml_file_rename)

            # Validate xml file.
            virt_xml_validate(special_vm_xml_file_rename)
            if virsh.define(special_vm_xml_file_rename).exit_status:
                test.cancel("can't create the domain")
        finally:
            if vm.is_alive():
                vm.destroy(gracefully=False)
            os.rename(first_disk_source_rename, first_disk_source)
            vmxml_backup.sync("--snapshots-metadata")
        return

    # Get device path.
    device_source_path = params.get("device_source_path", "")
    if source_path:
        device_source_path = data_dir.get_data_dir()

    # Prepare test environment.
    qemu_config = LibvirtQemuConfig()
    if test_disks_format:
        qemu_config.allow_disk_format_probing = True
        utils_libvirtd.libvirtd_restart()

    # Create virtual device file.
    disks = []
    try:
        for i in list(range(len(device_source_names))):
            if test_disk_type_dir:
                # If we testing disk type dir option,
                # it needn't to create disk image
                disks.append({"format": "dir",
                              "source": device_source_names[i]})
            else:
                path = "%s/%s.%s" % (device_source_path,
                                     device_source_names[i], device_formats[i])
                disk = prepare_disk(path, device_formats[i], devices[i], device_types[i])
                if disk:
                    disks.append(disk)

    except Exception as e:
        logging.error(repr(e))
        for img in disks:
            if "disk_dev" in img:
                if img["format"] == "nfs":
                    img["disk_dev"].cleanup()
            else:
                if img["format"] == "iscsi":
                    libvirt.setup_or_cleanup_iscsi(is_setup=False)
                if img["format"] not in ["dir", "scsi"]:
                    logging.debug("current source:%s", img["source"])
                    os.remove(img["source"])
                if img["format"] == "lvm":
                    clean_up_lvm()
        test.cancel("Creating disk failed")

    # Build disks xml.
    disks_xml = []
    # Additional disk images.
    disks_img = []
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    try:
        for i in list(range(len(disks))):
            disk_xml = Disk(type_name=device_types[i])
            # If we are testing image file on iscsi disk,
            # mount the disk and then create the image.
            if test_file_img_on_disk:
                mount_path = "/tmp/diskimg"
                if process.system("mkdir -p %s && mount %s %s"
                                  % (mount_path, disks[i]["source"],
                                     mount_path), ignore_status=True, shell=True):
                    test.cancel("Prepare disk failed")
                disk_path = "%s/%s.qcow2" % (mount_path, device_source_names[i])
                disk_source = libvirt.create_local_disk("file", disk_path, "1",
                                                        disk_format="qcow2")
                disks_img.append({"format": "qcow2",
                                  "source": disk_source, "path": mount_path})
            else:
                disk_source = disks[i]["source"]

            disk_xml.device = devices[i]

            if device_with_source:
                if device_types[i] == "file":
                    dev_attrs = "file"
                elif device_types[i] == "dir":
                    dev_attrs = "dir"
                else:
                    dev_attrs = "dev"
                source_dict = {dev_attrs: disk_source}
                if len(startup_policy) > i:
                    source_dict.update({"startupPolicy": startup_policy[i]})
                if auth_usage and not network_iscsi_baseimg:
                    auth_dict = {"auth_user": chap_user,
                                 "secret_type": secret_usage_type,
                                 "secret_usage": secret_usage_target}
                    disk_source = disk_xml.new_disk_source(
                                **disk_source)
                    disk_auth = disk_xml.new_auth(**auth_dict)
                    disk_source.auth = disk_auth
                    disk_xml.source = disk_source
                elif pool_type == "iscsi":
                    disk_xml.source = disk_xml.new_disk_source(
                        **disk_source)
                else:
                    disk_xml.source = disk_xml.new_disk_source(
                        **{"attrs": source_dict})
            if len(device_bootorder) > i:
                disk_xml.boot = device_bootorder[i]

            if test_block_size:
                disk_xml.blockio = {"logical_block_size": logical_block_size,
                                    "physical_block_size": physical_block_size}

            if len(wwn) != 0 and wwn[i] != "":
                disk_xml.wwn = wwn[i]
            if len(serial) != 0 and serial[i] != "":
                disk_xml.serial = serial[i]
            if vendor != "":
                disk_xml.vendor = vendor
            if product != "":
                disk_xml.product = product

            disk_xml.target = {"dev": device_targets[i], "bus": device_bus[i]}
            if len(device_readonly) > i:
                disk_xml.readonly = "yes" == device_readonly[i]

            if disk_transient:
                # After libvirt 6.9.0, transient disk feature is brought back on file based backend
                if libvirt_version.version_compare(6, 0, 0) and not libvirt_version.version_compare(6, 9, 0):
                    test.cancel("unsupported configuration: transient disks not supported")
                disk_xml.transient = "yes"

            # Add driver options from parameters
            driver_dict = {"name": "qemu"}
            if len(driver_options) > i:
                for driver_option in driver_options[i].split(','):
                    if driver_option != "":
                        d = driver_option.split('=')
                        driver_dict.update({d[0].strip(): d[1].strip()})
            disk_xml.driver = driver_dict

            def add_backingstore_element_to_disk_xml(disk_xml):
                """
                Add backingstore subelement of source element.
                :param disk_xml: the xml of disk
                """
                disk_xml.source = disk_xml.new_disk_source(
                        **{"attrs": source_dict})
                bs_source = {"protocol": "iscsi",
                             "name": "%s/%s" % (iscsi_target, lun_num),
                             "host": {"name": '127.0.0.1', "port": '3260'}}
                bs_dict = {"type": bs_device_types[i],
                           "format": {'type': bs_device_formats[i]}}
                new_bs = disk_xml.new_backingstore(**bs_dict)
                new_bs.source = disk_xml.BackingStore().new_source(**bs_source)
                if auth_usage:
                    auth_dict = {"auth_user": chap_user,
                                 "secret_type": secret_usage_type,
                                 "secret_usage": secret_usage_target}
                    disk_auth = disk_xml.new_auth(**auth_dict)
                    new_bs_source = new_bs.source
                    new_bs_source.auth = disk_auth
                    new_bs.source = new_bs_source
                disk_xml.backingstore = new_bs

            if network_iscsi_baseimg:
                add_backingstore_element_to_disk_xml(disk_xml)

            # Add disk address from parameters.
            if len(device_address) > i:
                addr_dict = {}
                for addr_option in device_address[i].split(','):
                    if addr_option != "":
                        d = addr_option.split('=')
                        addr_dict.update({d[0].strip(): d[1].strip()})
                disk_xml.address = disk_xml.new_disk_address(
                    **{"attrs": addr_dict})

            logging.debug("disk xml: %s", disk_xml)
            if hotplug:
                disks_xml.append(disk_xml)
            else:
                vmxml.add_device(disk_xml)

        # If we want to test with bootdisk.
        # just edit the bootdisk xml.
        if test_with_boot_disk:
            xml_devices = vmxml.devices
            disk_index = xml_devices.index(xml_devices.by_device_tag("disk")[0])
            disk = xml_devices[disk_index]
            if bootorder != "":
                disk.boot = bootorder
                osxml = vm_xml.VMOSXML()
                osxml.type = vmxml.os.type
                osxml.arch = vmxml.os.arch
                osxml.machine = vmxml.os.machine
                if test_boot_console:
                    osxml.loader = "/usr/share/seabios/bios.bin"
                    osxml.bios_useserial = "yes"
                    if utils_misc.compare_qemu_version(4, 0, 0, False):
                        osxml.bios_reboot_timeout = "-1"

                if vmxml.xmltreefile.find('features'):
                    vmxml_feature = vmxml.features
                    if vmxml_feature.has_feature('acpi') and 'aarch64' in arch:
                        osxml.loader = vmxml.os.loader
                        osxml.loader_readonly = vmxml.os.loader_readonly
                        osxml.loader_type = vmxml.os.loader_type

                del vmxml.os
                vmxml.os = osxml
            driver_dict = {"name": disk.driver["name"],
                           "type": disk.driver["type"]}
            if bootdisk_driver != "":
                for driver_option in bootdisk_driver.split(','):
                    if driver_option != "":
                        d = driver_option.split('=')
                        driver_dict.update({d[0].strip(): d[1].strip()})
            disk.driver = driver_dict

            if iface_driver != "":
                driver_dict = {"name": "vhost"}
                for driver_option in iface_driver.split(','):
                    if driver_option != "":
                        d = driver_option.split('=')
                        driver_dict.update({d[0].strip(): d[1].strip()})
                iface_list = xml_devices.by_device_tag("interface")[0]
                iface_index = xml_devices.index(iface_list)
                iface = xml_devices[iface_index]
                iface.driver = iface.new_driver(**{"driver_attr": driver_dict})
                iface.model = "virtio"
                del iface.address

            if bootdisk_snapshot != "":
                disk.snapshot = bootdisk_snapshot
                if device_readonly[0] == 'yes':
                    disk.readonly = True

            disk.target = {"dev": bootdisk_target, "bus": bootdisk_bus}
            device_source = disk.source.attrs["file"]

            del disk.address
            vmxml.devices = xml_devices
            vmxml.define()

        # Virt_disk_with_boot_nfs_pool check.
        if virt_disk_with_boot_nfs_pool:
            boot_iso_url = default_iso_url
            xml_devices = vmxml.devices
            first_disk_device = xml_devices.by_device_tag("disk")[0]
            vmxml.del_device(first_disk_device)
            if bootorder != "":
                osxml = vm_xml.VMOSXML()
                osxml.type = vmxml.os.type
                osxml.arch = vmxml.os.arch
                osxml.machine = vmxml.os.machine
                osxml.boots = ['cdrom']
                if test_boot_console:
                    osxml.loader = params.get("disk_boot_seabios", "")
                    osxml.bios_useserial = "yes"
                    if utils_misc.compare_qemu_version(4, 0, 0, False):
                        osxml.bios_reboot_timeout = "-1"
                del vmxml.os
                vmxml.os = osxml
            vmxml.sync()
            vm.start()
            check_boot_output()
            if vm.is_alive():
                vm.destroy(gracefully=False)
            return

        # Add virtio_scsi controller.
        if virtio_scsi_controller:
            scsi_controller = Controller("controller")
            scsi_controller.type = "scsi"
            ctl_type = params.get("virtio_scsi_controller_type")
            if ctl_type:
                scsi_controller.type = ctl_type
            scsi_controller.index = "0"
            ctl_index = params.get("virtio_scsi_controller_index")
            if ctl_index:
                scsi_controller.index = ctl_index
            ctl_model = params.get("virtio_scsi_controller_model")
            if ctl_model:
                scsi_controller.model = ctl_model
            if virtio_scsi_controller_driver != "":
                driver_dict = {}
                for driver_option in virtio_scsi_controller_driver.split(','):
                    if driver_option != "":
                        d = driver_option.split('=')
                        driver_dict.update({d[0].strip(): d[1].strip()})
                scsi_controller.driver = driver_dict
            if virtio_scsi_controller_addr != "":
                addr_dict = {}
                for addr_option in virtio_scsi_controller_addr.split(','):
                    if addr_option != "":
                        addr = addr_option.split('=')
                        addr_dict.update({addr[0].strip(): addr[1].strip()})
                scsi_controller.address = scsi_controller.new_controller_address(attrs=addr_dict)
            if ctl_type:
                vmxml.del_controller(ctl_type)
            else:
                vmxml.del_controller("scsi")
            vmxml.add_device(scsi_controller)

        # Create second disk,and attach to VM with the same index controller.
        if virt_disk_with_duplicate_scsi_controller_index:
            global custom_disk_xml
            disk_path = params.get("virt_disk_path", "")
            custom_disk_xml = create_addtional_disk('file', disk_path, device_formats[0], device_types[0],
                                                    devices[0], 'sdb',
                                                    device_bus[0])
            addr_dict = {'type': 'drive', 'controller': '0', 'bus': '0', 'target': '0', 'unit': '0'}
            custom_disk_xml.address = custom_disk_xml.new_disk_address(**{"attrs": addr_dict})
            # For cold plug,it expect attach succeed,but throw error: unsupported configuration:
            # Found duplicate drive address for disk when redefining this VM.
            if not hotplug:
                vmxml.sync()
                attach_option = '--config'
                ret = virsh.attach_device(vm_name, custom_disk_xml.xml,
                                          flagstr=attach_option, debug=True)
                # The change is introduced by block-dev feature,cold config the same index controller not allow now.
                if libvirt_version.version_compare(6, 0, 0):
                    libvirt.check_exit_status(ret, True)
                else:
                    libvirt.check_exit_status(ret)
                vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)

        # Create second controller,and add it to vmxml.
        if multi_ide_controller:
            for i in list(range(1, 2)):
                ide_controller = Controller("controller")
                ctl_type = params.get("virtio_scsi_controller_type")
                ide_controller.type = ctl_type
                ide_controller.index = str(i)
                vmxml.add_device(ide_controller)

        # Test usb devices.
        usb_devices = {}
        if add_usb_device:
            # Delete all usb devices first.
            controllers = vmxml.get_devices(device_type="controller")
            for ctrl in controllers:
                if ctrl.type == "usb":
                    vmxml.del_device(ctrl)

            inputs = vmxml.get_devices(device_type="input")
            for input_device in inputs:
                if input_device.type_name == "tablet":
                    vmxml.del_device(input_device)

            hubs = vmxml.get_devices(device_type="hub")
            for hub in hubs:
                if hub.type_name == "usb":
                    vmxml.del_device(hub)

            # Add new usb controllers.
            usb_controller1 = Controller("controller")
            usb_controller1.type = "usb"
            usb_controller1.index = "0"
            usb_controller1.model = "piix3-uhci"
            vmxml.add_device(usb_controller1)
            usb_controller2 = Controller("controller")
            usb_controller2.type = "usb"
            usb_controller2.index = "1"
            usb_controller2.model = "ich9-ehci1"
            vmxml.add_device(usb_controller2)

            input_obj = Input("tablet")
            input_obj.input_bus = "usb"
            vmxml.add_device(input_obj)
            usb_devices.update({"input": None})

            hub_obj = Hub("usb")
            vmxml.add_device(hub_obj)
            usb_devices.update({"hub": None})

        if dom_iothreads:
            # Delete cputune/iothreadids section, it may have conflict
            # with domain iothreads
            del vmxml.cputune
            del vmxml.iothreadids
            vmxml.iothreads = int(dom_iothreads)
        vmxml.sync()
        # Test snapshot before vm start.
        if test_disk_snapshot:
            if snapshot_before_start:
                ret = virsh.snapshot_create_as(vm_name, "s1 %s"
                                               % snapshot_option)
                libvirt.check_exit_status(ret, snapshot_error)

        if virt_use_nfs_off:
            utils_selinux.set_status("enforcing")
            result = process.run("setsebool virt_use_nfs off", shell=True, ignore_status=True)
            if result.exit_status:
                logging.info("Failed to set virt_use_nfs off")

        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        # Start the VM.
        logging.debug("Starting VM: %s", vmxml)
        vm.start()
        if test_minimal_xml:
            return
        vm.wait_for_login()
        if status_error:
            test.fail("VM started unexpectedly")

        def add_sleep(sleep_seconds=10):
            """
            Check specific arch and do sleep for specified seconds.
            This is to workaround for some failures due to different
            VM response.

            :param sleep_seconds: seconds for sleeping
            """
            if on_ppc:
                time.sleep(sleep_seconds)

        def test_device_update_boot_order(disk_type, disk_path, error_msg):
            """
            Wrap device update boot order steps here.
            Firstly,create another disk for a given path.
            Update previous disk with newly created disk and modified boot order.
            Eject disk finally.

            :param disk_type: the type of disk.
            :param disk_path: the path of disk.
            :param error_msg: the expected error message.
            """
            addtional_disk = create_addtional_disk(disk_type, disk_path, device_formats[0], device_types[0],
                                                   devices[0], device_targets[0],
                                                   device_bus[0])
            addtional_disk.boot = '2'
            addtional_disk.readonly = 'True'
            # Update disk cdrom/floppy with modified boot order,it expect fail.
            flopy_error_msg = params.get('flopy_error_msg', "")
            cdrom_error_msg = params.get('cdrom_error_msg', "")
            try:
                stderr_output = virsh.update_device(vm_name, addtional_disk.xml, debug=True).stderr_text
            except Exception as update_device_exception:
                if all(not stderr_output.count(flopy_error_msg),
                       not stderr_output.count(cdrom_error_msg)):
                    test.fail(error_msg)
            # Force eject cdrom or floppy, it expect succeed.
            eject_disk = Disk(type_name='block')
            eject_disk.target = {"dev": device_targets[0], "bus": device_bus[0]}
            eject_disk.device = devices[0]
            ret = virsh.update_device(vm_name, eject_disk.xml, flagstr='--force', debug=True)
            libvirt.check_exit_status(ret)

        if disk_cdrom_update_boot_order:
            check_partitions = False
            disk_path = params.get('iso_path', "")
            test_device_update_boot_order("iso", disk_path, 'cdrom update error happen...')

        if disk_floppy_update_boot_order:
            check_partitions = False
            disk_path = params.get('floppy_path', "")
            test_device_update_boot_order("floppy", disk_path, 'floppy update error happen...')

        # Hotplug the disks.
        if device_at_dt_disk:
            for i in list(range(len(disks))):
                attach_option = ""
                if len(device_attach_option) > i:
                    attach_option = device_attach_option[i]
                logging.debug('debug xml output:')
                dump_vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
                logging.debug('vm:%s', dump_vmxml)
                ret = virsh.attach_disk(vm_name, disks[i]["source"],
                                        device_targets[i],
                                        attach_option, debug=True)
                disk_attach_error = False
                if len(device_attach_error) > i:
                    disk_attach_error = "yes" == device_attach_error[i]
                libvirt.check_exit_status(ret, disk_attach_error)
            if attach_ccw_address_at_dt_disk:
                attach_option = device_attach_option[0].replace('--live', '--config')
                ret = virsh.attach_disk(vm_name, disks[0]["source"],
                                        device_targets[0],
                                        attach_option, debug=True)
                disk_attach_error = False
                libvirt.check_exit_status(ret, disk_attach_error)
                vm.destroy(gracefully=False)
                status_error = True
                vm.start()
        elif hotplug:
            for i in list(range(len(disks_xml))):
                disks_xml[i].xmltreefile.write()
                attach_option = ""
                if len(device_attach_option) > i:
                    attach_option = device_attach_option[i]
                ret = virsh.attach_device(vm_name, disks_xml[i].xml,
                                          flagstr=attach_option)
                attach_error = False
                logging.debug(vm_xml.VMXML.new_from_dumpxml(vm_name))
                if len(device_attach_error) > i:
                    attach_error = "yes" == device_attach_error[i]
                libvirt.check_exit_status(ret, attach_error)

            # If hotplug one disk with same controller index, it expect throw
            # internal error: unable to execute QEMU command '__com.redhat_drive_add': Duplicate ID '.
            if virt_disk_with_duplicate_scsi_controller_index:
                attach_option = '--live'
                attach_error = True
                ret = virsh.attach_device(vm_name, custom_disk_xml.xml,
                                          flagstr=attach_option, debug=True)
                libvirt.check_exit_status(ret, attach_error)

            if attach_device_as_scsi_lun:
                attach_options = ['--live', '--current', '--config', '--persistent']
                attach_error = True
                for counter in range(2):
                    if counter > 0:
                        if vm.is_alive():
                            vm.destroy(gracefully=False)
                    for attach_option in attach_options:
                        ret = virsh.attach_device(vm_name, disks_xml[0].xml,
                                                  flagstr=attach_option, debug=False)
                        libvirt.check_exit_status(ret, attach_error)
                return

    except virt_vm.VMStartError as details:
        if not status_error:
            test.fail('VM failed to start:\n%s' % details)
        # If usb error message not contain 'unexpected address type for usb disk', fail this case.
        usb_error_message = params.get('usb_error_message')
        if usb_error_message and not str(details).count(usb_error_message):
            test.fail('VM error message should contain:\n%s' % usb_error_message)
    except xcepts.LibvirtXMLError as xml_error:
        if not define_error:
            test.fail("Failed to define VM:\n%s" % xml_error)
        # Validate VM xml.
        if virt_xml_validate_test:
            virt_xml_validate(vmxml.xml, validate_error)
    else:
        # Validate VM xml.
        if virt_xml_validate_test:
            virt_xml_validate(vmxml.xml, validate_error)
        # VM is started, perform the tests.
        if test_slots_order:
            if not check_disk_order(device_targets):
                test.fail("Disks slots order error in domain xml")

        if test_disks_format:
            if not check_disk_format(device_targets, device_formats):
                test.fail("Disks type error in VM xml")

        if test_boot_console:
            # Check if disks bootorder is as expected.
            expected_order = params.get("expected_order").split(',')
            if not check_boot_console(expected_order):
                test.fail("Test VM bootorder failed")

        if test_block_size:
            # Check disk block size in VM.
            if not check_vm_block_size(device_targets,
                                       logical_block_size, physical_block_size):
                test.fail("Test disk block size in VM failed")

        if test_disk_option_cmd:
            # Check if disk options take affect in qemu commmand line.
            cmd = ("ps -ef | grep %s | grep -v grep " % vm_name)
            logging.debug("VM cmdline: %s", process.system_output(cmd, shell=True))
            if test_with_boot_disk:
                d_target = bootdisk_target
            else:
                d_target = device_targets[0]
                if device_with_source:
                    # After blockdev introduced on libvirt 5.6.0 afterwards, below step is not needed.
                    if not libvirt_version.version_compare(6, 0, 0):
                        cmd += (" | grep %s" %
                                (device_source_names[0].replace(',', ',,')))
            io = vm_xml.VMXML.get_disk_attr(vm_name, d_target, "driver", "io")
            if io:
                cmd += " | grep .*aio.*%s.*" % io
            ioeventfd = vm_xml.VMXML.get_disk_attr(vm_name, d_target,
                                                   "driver", "ioeventfd")
            if ioeventfd:
                cmd += " | grep ioeventfd=%s" % ioeventfd
            event_idx = vm_xml.VMXML.get_disk_attr(vm_name, d_target,
                                                   "driver", "event_idx")
            if event_idx:
                cmd += " | grep event_idx=%s" % event_idx

            discard = vm_xml.VMXML.get_disk_attr(vm_name, d_target,
                                                 "driver", "discard")
            if discard:
                cmd += " | grep .*discard.*%s.*" % discard
            copy_on_read = vm_xml.VMXML.get_disk_attr(vm_name, d_target,
                                                      "driver", "copy_on_read")
            # After blockdev introduced, if copy_on_read == "off", nothing is shown up in qemu command output
            # And if copy_on_read == "on", the output is packaged in json format characterized with key=value
            if copy_on_read:
                # The change is introduced by block-dev feature.
                if libvirt_version.version_compare(6, 0, 0):
                    if copy_on_read == "on":
                        cmd += " | grep .*driver.*copy-on-read.*"
                    else:
                        # ignore the checking if copy_on_read == "off"
                        pass
                else:
                    cmd += " | grep copy-on-read=%s" % copy_on_read

            iothread = vm_xml.VMXML.get_disk_attr(vm_name, d_target,
                                                  "driver", "iothread")
            if iothread:
                cmd += " | grep iothread=iothread%s" % iothread

            if len(serial) != 0 and serial[0] != "":
                cmd += " | grep serial=%s" % serial[0]
            if len(wwn) != 0 and wwn[0] != "":
                if len(wwn) > 1:
                    cmd += " | grep -E \"wwn=(0x)?%s.*wwn=(0x)?%s\"" % (wwn[0], wwn[1])
                else:
                    cmd += " | grep -E \"wwn=(0x)?%s\"" % wwn[0]
            if vendor != "":
                cmd += " | grep vendor=%s" % vendor
            if product != "":
                cmd += " | grep \"product=%s\"" % product

            num_queues = ""
            ioeventfd = ""
            cmd_per_lun = ""
            max_sectors = ""
            if virtio_scsi_controller_driver != "":
                for driver_option in virtio_scsi_controller_driver.split(','):
                    if driver_option != "":
                        d = driver_option.split('=')
                        if d[0].strip() == "queues":
                            num_queues = d[1].strip()
                        elif d[0].strip() == "ioeventfd":
                            ioeventfd = d[1].strip()
                        elif d[0].strip() == "cmd_per_lun":
                            cmd_per_lun = d[1].strip()
                        elif d[0].strip() == "max_sectors":
                            max_sectors = d[1].strip()
            if num_queues != "":
                cmd += " | grep num_queues=%s" % num_queues
            if ioeventfd:
                cmd += " | grep ioeventfd=%s" % ioeventfd
            if cmd_per_lun:
                cmd += " | grep cmd_per_lun=%s" % cmd_per_lun
            if max_sectors:
                cmd += " | grep max_sectors=%s" % max_sectors
            iface_event_idx = ""
            if iface_driver != "":
                for driver_option in iface_driver.split(','):
                    if driver_option != "":
                        d = driver_option.split('=')
                        if d[0].strip() == "event_idx":
                            iface_event_idx = d[1].strip()
            if iface_event_idx != "":
                driver = "virtio-net-pci"
                if 's390x' in arch:
                    driver = "virtio-net-ccw"
                cmd += " | grep %s,event_idx=%s" % (driver, iface_event_idx)

            if process.system(cmd, ignore_status=True, shell=True):
                test.fail("Check disk driver option failed with %s" % cmd)

        if test_disk_snapshot:
            ret = virsh.snapshot_create_as(vm_name, "s1 %s" % snapshot_option, debug=True)
            libvirt.check_exit_status(ret, snapshot_error)

        # Check the disk bootorder.
        if test_disk_bootorder:
            for i in list(range(len(device_targets))):
                if len(device_attach_error) > i:
                    if device_attach_error[i] == "yes":
                        continue
                if device_bootorder[i] != vm_xml.VMXML.get_disk_attr(
                        vm_name, device_targets[i], "boot", "order"):
                    test.fail("Check bootorder failed")

        # Check disk bootorder with snapshot.
        if test_disk_bootorder_snapshot:
            disk_name = os.path.splitext(device_source)[0]
            check_bootorder_snapshot(disk_name)

        # Check disk readonly option.
        if test_disk_readonly:
            if not check_readonly(device_targets):
                test.fail("Checking disk readonly option failed")

        # Check disk bus device option in qemu command line.
        if test_bus_device_option:
            cmd = ("ps -ef | grep %s | grep -v grep " % vm_name)
            logging.debug("Qemu cmdline: %s",
                          process.system_output(cmd, ignore_status=True, shell=True))
            if "s390" in arch and device_bus[0] == "virtio" and not devices[0] == "lun":
                dev_devno = vm_xml.VMXML.get_disk_attr(vm_name, device_targets[0],
                                                       "address", "devno").replace("0x", "")
                dev_id_prefix = "fe.0."
                device_option = "scsi=off"

                cmd += (" | grep virtio-blk-ccw,%s,devno=%s%s"
                        % (device_option, dev_id_prefix, dev_devno))

                if device_bus[0] == 'scsi':
                    dev_id = vm_xml.VMXML.get_disk_attr(vm_name, device_targets[0],
                                                        "alias", "name")
                    cmd += " | grep drive.*id=%s" % dev_id
            else:
                dev_bus = int(vm_xml.VMXML.get_disk_attr(vm_name, device_targets[0],
                                                         "address", "bus"), 16)
                if device_bus[0] == "virtio":
                    pci_slot = int(vm_xml.VMXML.get_disk_attr(vm_name, device_targets[0],
                                                              "address", "slot"), 16)
                    if devices[0] == "lun":
                        device_option = "scsi=on"
                    else:
                        device_option = "scsi=off"
                    # scsi=on/off flag is removed from qemu command line after libvirt 6.6.0, so update cmd to make code compatible.
                    if libvirt_version.version_compare(6, 6, 0):
                        cmd += (" | grep virtio-blk-pci,bus=pci.%x,addr=0x%x"
                                % (dev_bus, pci_slot))
                    else:
                        cmd += (" | grep virtio-blk-pci,%s,bus=pci.%x,addr=0x%x"
                                % (device_option, dev_bus, pci_slot))
                if device_bus[0] in ["ide", "sata", "scsi"]:
                    dev_unit = int(vm_xml.VMXML.get_disk_attr(vm_name, device_targets[0],
                                                              "address", "unit"), 16)
                    dev_id = vm_xml.VMXML.get_disk_attr(vm_name, device_targets[0],
                                                        "alias", "name")
                if device_bus[0] == "ide":
                    if devices[0] == "cdrom":
                        device_option = "ide-cd"
                    else:
                        device_option = "ide-hd"
                    cmd += (" | grep %s,bus=ide.%d,unit=%d,drive=drive-%s,id=%s"
                            % (device_option, dev_bus, dev_unit, dev_id, dev_id))
                if device_bus[0] == "sata":
                    # After block-dev feature introduced, the qemu output is changed accordingly with dev_bus only.
                    if libvirt_version.version_compare(6, 0, 0):
                        cmd += (" | grep '.*%s'" % dev_bus)
                    else:
                        cmd += (" | grep 'device ahci,.*,bus=pci.%s'" % dev_bus)
                if device_bus[0] == "scsi":
                    if devices[0] == "lun":
                        device_option = "scsi-block"
                    elif devices[0] == "cdrom":
                        device_option = "scsi-cd"
                    else:
                        device_option = "scsi-hd"
                    cmd += (" | grep %s,bus=scsi%d.%d,.*drive=.*,id=%s"
                            % (device_option, dev_bus, dev_unit, dev_id))
                if device_bus[0] == "usb":
                    dev_port = vm_xml.VMXML.get_disk_attr(vm_name, device_targets[0],
                                                          "address", "port")
                    dev_id = vm_xml.VMXML.get_disk_attr(vm_name, device_targets[0],
                                                        "alias", "name")
                    if devices[0] == "disk":
                        # For dev_bus ==0,it hardcode bus=usb.0,other than usb%s.0.
                        usb_bus_str = "usb%s.0" % dev_bus
                        if dev_bus == 0:
                            usb_bus_str = "usb.0"
                        cmd += (" | grep usb-storage,bus=%s,port=%s,"
                                "drive=.*,id=%s"
                                % (usb_bus_str, dev_port, dev_id))
                    if "input" in usb_devices:
                        input_addr = get_device_addr('input', 'tablet')
                        cmd += (" | grep usb-tablet,id=input[0-9],bus=usb.%s,"
                                "port=%s" % (input_addr["bus"],
                                             input_addr["port"]))
                    if "hub" in usb_devices:
                        hub_addr = get_device_addr('hub', 'usb')
                        cmd += (" | grep usb-hub,id=hub0,bus=usb.%s,"
                                "port=%s" % (hub_addr["bus"],
                                             hub_addr["port"]))

            time.sleep(1)
            if process.system(cmd, ignore_status=True, shell=True):
                test.fail("Can not see disk option"
                          " in command line")

        if dom_iothreads:
            check_dom_iothread()

        # Check disk cache mode.
        if check_disk_cache:
            check_disk_cache_mode(device_targets[0], disk_cache_mode)

        # Check in VM after command.
        if check_partitions:
            if not check_vm_partitions(devices,
                                       device_targets):
                test.fail("Can not see device in VM")

        # Check discard in VM after command.
        if check_discard:
            check_vm_discard(device_targets[0])

        # Check pci bridge in VM after command.
        if check_pci_bridge:
            check_vm_pci_bridge()

        # Check disk save and restore.
        if test_disk_save_restore:
            save_file = "/tmp/%s.save" % vm_name
            check_disk_save_restore(save_file, device_targets,
                                    startup_policy)
            if os.path.exists(save_file):
                os.remove(save_file)
        if restart_libvird:
            if not utils_libvirtd.libvirtd_restart():
                test.fail('Libvirtd is expected to be started')
            vm.wait_for_login()
        if disk_transient:
            check_transient_disk_keyword()
            check_restart_transient_vm(device_targets[0])
        # If we testing hotplug, detach the disk at last.
        if device_at_dt_disk:
            for i in list(range(len(disks))):
                dt_options = ""
                if devices[i] == "cdrom":
                    dt_options = "--config"
                ret = virsh.detach_disk(vm_name, device_targets[i],
                                        dt_options, wait_remove_event=True,
                                        **virsh_dargs)
                disk_detach_error = False
                if len(device_attach_error) > i:
                    disk_detach_error = "yes" == device_attach_error[i]
                if disk_detach_error:
                    libvirt.check_exit_status(ret, disk_detach_error)
                else:
                    libvirt.check_exit_status(ret)

                def _check_disk_detach():
                    try:
                        session = vm.wait_for_login()
                        if device_targets[i] not in utils_disk.get_parts_list():
                            return True
                        else:
                            logging.debug("still can find device target after detaching")
                    except Exception:
                        return False
                # If disk_detach_error is False, then wait for seconds to let detach operation accomplish complete
                if not disk_detach_error:
                    utils_misc.wait_for(_check_disk_detach, timeout=20)
                # Give time to log file to collect more events
                if virtio_disk_hot_unplug_event_watch:
                    result = utils_misc.wait_for(lambda: check_info_in_libvird_log_file('"event": "DEVICE_DELETED"'), timeout=20)
                    if not result:
                        test.fail("Failed to get expected messages from log file: %s."
                                  % log_config_path)
            # Check disks in VM after hotunplug.
            if check_partitions_hotunplug:
                if not check_vm_partitions(devices,
                                           device_targets, False):
                    test.fail("See device in VM after hotunplug")
        elif device_cold_dt:
            ret = virsh.detach_disk(vm_name, device_targets[0],
                                    '--config', **virsh_dargs)
            libvirt.check_exit_status(ret)
            ret = virsh.domblklist(vm_name, '--inactive',
                                   ignore_status=False, debug=True)
            target_disks = re.findall(r"[v,s]d[a-z]", ret.stdout.strip())
            if len(target_disks) > 1:
                test.fail("Fail to cold unplug disks. ")
        elif hotplug:
            # Test attach device multiple iteration
            if test_attach_device_iteration:
                attach_option = device_attach_option[0]
                iteration_times = int(params.get("iteration_times", ""))
                for counter in range(0, iteration_times):
                    logging.info("Begin to execute attach or detach %d operations", counter)
                    ret = virsh.detach_device(vm_name, disks_xml[0].xml,
                                              flagstr=attach_option, debug=True, wait_remove_event=True)
                    libvirt.check_exit_status(ret)
                    # Sleep 10 seconds to let VM really cleanup devices.
                    time.sleep(10)
                    ret = virsh.attach_device(vm_name, disks_xml[0].xml,
                                              flagstr=attach_option, debug=True)
                    libvirt.check_exit_status(ret)
                    # Check VM partitions on each 20 attempts.
                    if counter % 20 == 0:
                        # Sleep 30 seconds to allow disk partitions can really be detected in VM internal.
                        time.sleep(30)
                        if not check_vm_partitions(devices, device_targets) and not check_vm_partitions(devices, 'vdc'):
                            test.fail("Can not see device in VM when attaching disk in %d times" % counter)

            for i in list(range(len(disks_xml))):
                if len(device_attach_error) > i:
                    if device_attach_error[i] == "yes":
                        continue
                ret = virsh.detach_device(vm_name, disks_xml[i].xml,
                                          flagstr=attach_option, wait_remove_event=True, **virsh_dargs)
                os.remove(disks_xml[i].xml)
                libvirt.check_exit_status(ret)

            # Check disks in VM after hotunplug.
            if check_partitions_hotunplug:
                if not check_vm_partitions(devices,
                                           device_targets, False):
                    test.fail("See device in VM after hotunplug")

    finally:
        # Delete snapshots.
        if virsh.domain_exists(vm_name):
            #To Delet snapshot, destroy vm first.
            if vm.is_alive():
                vm.destroy()
            libvirt.clean_up_snapshots(vm_name, domxml=vmxml_backup)

        # Recover VM.
        if vm.is_alive():
            vm.destroy(gracefully=False)
        vmxml_backup.sync("--snapshots-metadata")

        # Restore qemu_config file.
        qemu_config.restore()
        utils_libvirtd.libvirtd_restart()

        # Restore libvirtd config file.
        if virtio_disk_hot_unplug_event_watch:
            libvirtd_config.restore()

        # Restore selinux and virt_use_nfs
        if virt_use_nfs_off:
            utils_selinux.set_status(selinux_mode)
            result = process.run("setsebool virt_use_nfs %s" % backup_virt_use_nfs_status,
                                 shell=True)
            if result.exit_status:
                logging.info("Failed to restore virt_use_nfs value")

        for img in disks_img:
            if os.path.exists(img["path"]):
                process.run("umount %s && rmdir %s"
                            % (img["path"], img["path"]), ignore_status=True, shell=True)

        for img in disks:
            if "disk_dev" in img:
                if img["format"] == "nfs":
                    img["disk_dev"].cleanup()

                del img["disk_dev"]
            else:
                if img["format"] == "scsi":
                    utils_misc.wait_for(libvirt.delete_scsi_disk,
                                        120, ignore_errors=True)
                elif img["format"] == "iscsi" or network_iscsi_baseimg:
                    libvirt.setup_or_cleanup_iscsi(is_setup=False)
                    # Clean up secret
                    if auth_usage and secret_uuid:
                        virsh.secret_undefine(secret_uuid)
                    if pool_type:
                        pvt.cleanup_pool(pool_name, pool_type, pool_target,
                                         emulated_image, **virsh_dargs)
                elif img["format"] == "lvm":
                    clean_up_lvm()
                elif pool_type and pool_type == "netfs":
                    pvt.cleanup_pool(pool_name, pool_type, pool_target,
                                     emulated_image, **virsh_dargs)
                elif img["format"] not in ["dir"]:
                    if file_mount_point_type:
                        process.run("umount %s && rm -rf  %s" % (tmp_demo_img, tmp_demo_img), ignore_status=True, shell=True)
                    if os.path.exists(img["source"]):
                        os.remove(img["source"])
