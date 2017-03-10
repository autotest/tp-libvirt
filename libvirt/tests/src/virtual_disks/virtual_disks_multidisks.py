import os
import re
import json
import logging

import aexpect

from avocado.utils import process
from avocado.core import exceptions

from virttest import virt_vm
from virttest import virsh
from virttest import remote
from virttest import nfs
from virttest import utils_libvirtd
from virttest import utils_misc
from virttest.utils_test import libvirt
from virttest.utils_config import LibvirtQemuConfig
from virttest.libvirt_xml import vm_xml, xcepts
from virttest.libvirt_xml.devices.disk import Disk
from virttest.libvirt_xml.devices.input import Input
from virttest.libvirt_xml.devices.hub import Hub
from virttest.libvirt_xml.devices.controller import Controller

from provider import libvirt_version


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

    def check_disk_order(targets_name):
        """
        Check VM disk's order on pci bus.

        :param targets_name. Disks target list.
        :return: True if check successfully.
        """
        logging.info("Checking VM disks order...")
        xml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        disk_list = xml.devices.by_device_tag("disk")
        slot_dict = {}
        # Get the disks pci slot.
        for disk in disk_list:
            if 'virtio' == disk.target['bus']:
                slot_dict[disk.target['dev']] = int(
                    disk.address.attrs['slot'], base=16)
        # Disk's order on pci bus should keep the same with disk target name.
        s_dev = sorted(slot_dict.keys())
        s_slot = sorted(slot_dict.values())
        for i in range(len(s_dev)):
            if s_dev[i] in targets_name and slot_dict[s_dev[i]] != s_slot[i]:
                return False
        return True

    def setup_nfs_disk(disk_name, disk_type, disk_format="raw"):
        """
        Setup nfs disk.
        """
        mount_src = os.path.join(test.tmpdir, "nfs-export")
        if not os.path.exists(mount_src):
            os.mkdir(mount_src)
        mount_dir = os.path.join(test.tmpdir, "nfs-mount")

        if disk_type in ["file", "floppy", "iso"]:
            disk_path = "%s/%s" % (mount_src, disk_name)
            device_source = libvirt.create_local_disk(disk_type, disk_path, "2",
                                                      disk_format=disk_format)
            #Format the disk.
            if disk_type in ["file", "floppy"]:
                cmd = ("mkfs.ext3 -F %s && setsebool virt_use_nfs true"
                       % device_source)
                if process.run(cmd, ignore_status=True, shell=True).exit_status:
                    raise exceptions.TestSkipError("Format disk failed")

        nfs_params = {"nfs_mount_dir": mount_dir, "nfs_mount_options": "ro",
                      "nfs_mount_src": mount_src, "setup_local_nfs": "yes",
                      "export_options": "rw,no_root_squash"}

        nfs_obj = nfs.Nfs(nfs_params)
        nfs_obj.setup()
        if not nfs_obj.mount():
            return None

        disk = {"disk_dev": nfs_obj, "format": "nfs", "source":
                "%s/%s" % (mount_dir, os.path.split(device_source)[-1])}

        return disk

    def prepare_disk(path, disk_format):
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
                raise exceptions.TestSkipError("Get scsi disk failed")

        elif disk_format in ["iso", "floppy"]:
            disk_path = libvirt.create_local_disk(disk_format, path)
            disk.update({"format": disk_format,
                         "source": disk_path})
        elif disk_format == "nfs":
            nfs_disk_type = params.get("nfs_disk_type", None)
            disk.update(setup_nfs_disk(os.path.split(path)[-1], nfs_disk_type))

        elif disk_format == "iscsi":
            # Create iscsi device if needed.
            image_size = params.get("image_size", "2G")
            device_source = libvirt.setup_or_cleanup_iscsi(
                is_setup=True, is_login=True, image_size=image_size)
            logging.debug("iscsi dev name: %s", device_source)

            # Format the disk and make file system.
            libvirt.mk_part(device_source)
            # Run partprobe to make the change take effect.
            process.run("partprobe", ignore_status=True, shell=True)
            libvirt.mkfs("%s1" % device_source, "ext3")
            device_source += "1"
            disk.update({"format": disk_format,
                         "source": device_source})
        elif disk_format in ["raw", "qcow2"]:
            disk_size = params.get("virt_disk_device_size", "1")
            device_source = libvirt.create_local_disk(
                "file", path, disk_size, disk_format=disk_format)
            disk.update({"format": disk_format,
                         "source": device_source})

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
            for i in range(len(devices)):
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
        except (remote.LoginError, virt_vm.VMError, aexpect.ShellError), e:
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
        except (remote.LoginError, virt_vm.VMError, aexpect.ShellError), e:
            logging.error(str(e))
            return False

    def check_readonly(targets_name):
        """
        Check disk readonly option.
        """
        logging.info("Checking disk readonly option...")
        try:
            session = vm.wait_for_login()
            for target in targets_name:
                if target == "hdc":
                    mount_cmd = "mount /dev/cdrom /mnt"
                elif target == "fda":
                    mount_cmd = "modprobe floppy && mount /dev/fd0 /mnt"
                else:
                    mount_cmd = "mount /dev/%s /mnt" % target
                cmd = ("(%s && ls /mnt || exit 1) && (echo "
                       "'test' > /mnt/test || umount /mnt)" % mount_cmd)
                s, o = session.cmd_status_output(cmd)
                logging.debug("cmd exit: %s, output: %s", s, o)
                if s:
                    session.close()
                    return False
            session.close()
            return True
        except (remote.LoginError, virt_vm.VMError, aexpect.ShellError), e:
            logging.error(str(e))
            return False

    def check_bootorder_snapshot(disk_name):
        """
        Check VM disk's bootorder option with snapshot.

        :param disk_name. The target disk to be checked.
        """
        logging.info("Checking diskorder option with snapshot...")
        snapshot1 = "s1"
        snapshot2 = "s2"
        snapshot2_file = os.path.join(test.tmpdir, "s2")
        ret = virsh.snapshot_create(vm_name, "", **virsh_dargs)
        libvirt.check_exit_status(ret)

        ret = virsh.snapshot_create_as(vm_name, "%s --disk-only" % snapshot1,
                                       **virsh_dargs)
        libvirt.check_exit_status(ret)

        ret = virsh.snapshot_dumpxml(vm_name, snapshot1)
        libvirt.check_exit_status(ret)

        cmd = "echo \"%s\" | grep %s.%s" % (ret.stdout, disk_name, snapshot1)
        if process.run(cmd, ignore_status=True, shell=True).exit_status:
            raise exceptions.TestError("Check snapshot disk failed")

        ret = virsh.snapshot_create_as(vm_name,
                                       "%s --memspec file=%s,snapshot=external"
                                       % (snapshot2, snapshot2_file),
                                       **virsh_dargs)
        libvirt.check_exit_status(ret)

        ret = virsh.dumpxml(vm_name)
        libvirt.check_exit_status(ret)

        cmd = ("echo \"%s\" | grep -A 16 %s.%s | grep \"boot order='%s'\""
               % (ret.stdout, disk_name, snapshot2, bootorder))
        if process.run(cmd, ignore_status=True, shell=True).exit_status:
            raise exceptions.TestError("Check snapshot disk with bootorder failed")

        snap_lists = virsh.snapshot_list(vm_name)
        if snapshot1 not in snap_lists or snapshot2 not in snap_lists:
            raise exceptions.TestError("Check snapshot list failed")

        # Check virsh save command after snapshot.
        save_file = "/tmp/%s.save" % vm_name
        ret = virsh.save(vm_name, save_file, **virsh_dargs)
        libvirt.check_exit_status(ret)

        # Check virsh restore command after snapshot.
        ret = virsh.restore(save_file, **virsh_dargs)
        libvirt.check_exit_status(ret)

        #Passed all test.
        os.remove(save_file)

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
        for i in range(len(bootorders)):
            if lines[i] != bootorders[i]:
                return False

        return True

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
            s, o = session.cmd_status_output(cmd)
            if s:
                session.close()
                raise exceptions.TestError("Failed to read/write disk in VM:"
                                           " %s" % o)
            session.close()
        except (remote.LoginError, virt_vm.VMError, aexpect.ShellError), e:
            raise exceptions.TestError(str(e))

    def check_dom_iothread():
        """
        Check iothread by qemu-monitor-command.
        """
        ret = virsh.qemu_monitor_command(vm_name,
                                         '{"execute": "query-iothreads"}',
                                         "--pretty")
        libvirt.check_exit_status(ret)
        logging.debug("Domain iothreads: %s", ret.stdout)
        iothreads_ret = json.loads(ret.stdout)
        if len(iothreads_ret['return']) != int(dom_iothreads):
            raise exceptions.TestFail("Failed to check domain iothreads")

    status_error = "yes" == params.get("status_error", "no")
    define_error = "yes" == params.get("define_error", "no")
    dom_iothreads = params.get("dom_iothreads")

    # Disk specific attributes.
    devices = params.get("virt_disk_device", "disk").split()
    device_source_names = params.get("virt_disk_device_source").split()
    device_targets = params.get("virt_disk_device_target", "vda").split()
    device_formats = params.get("virt_disk_device_format", "raw").split()
    device_types = params.get("virt_disk_device_type", "file").split()
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
    serial = params.get("virt_disk_serial", "")
    wwn = params.get("virt_disk_wwn", "")
    vendor = params.get("virt_disk_vendor", "")
    product = params.get("virt_disk_product", "")
    add_disk_driver = params.get("add_disk_driver")
    iface_driver = params.get("iface_driver_option", "")
    bootdisk_snapshot = params.get("bootdisk_snapshot", "")
    snapshot_option = params.get("snapshot_option", "")
    snapshot_error = "yes" == params.get("snapshot_error", "no")
    add_usb_device = "yes" == params.get("add_usb_device", "no")
    input_usb_address = params.get("input_usb_address", "")
    hub_usb_address = params.get("hub_usb_address", "")
    hotplug = "yes" == params.get(
        "virt_disk_device_hotplug", "no")
    device_at_dt_disk = "yes" == params.get("virt_disk_at_dt_disk", "no")
    device_with_source = "yes" == params.get(
        "virt_disk_with_source", "yes")
    virtio_scsi_controller = "yes" == params.get(
        "virtio_scsi_controller", "no")
    virtio_scsi_controller_driver = params.get(
        "virtio_scsi_controller_driver", "")
    source_path = "yes" == params.get(
        "virt_disk_device_source_path", "yes")
    check_patitions = "yes" == params.get(
        "virt_disk_check_partitions", "yes")
    check_patitions_hotunplug = "yes" == params.get(
        "virt_disk_check_partitions_hotunplug", "yes")
    test_slots_order = "yes" == params.get(
        "virt_disk_device_test_order", "no")
    test_disks_format = "yes" == params.get(
        "virt_disk_device_test_format", "no")
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

    if dom_iothreads:
        if not libvirt_version.version_compare(1, 2, 8):
            raise exceptions.TestSkipError("iothreads not supported for"
                                           " this libvirt version")

    if test_block_size:
        logical_block_size = params.get("logical_block_size")
        physical_block_size = params.get("physical_block_size")

    if any([test_boot_console, add_disk_driver]):
        if vm.is_dead():
            vm.start()
        session = vm.wait_for_login()
        if test_boot_console:
            # Setting console to kernel parameters
            vm.set_kernel_console("ttyS0", "115200")
        if add_disk_driver:
            # Ignore errors here
            session.cmd("dracut --force --add-drivers '%s'"
                        % add_disk_driver)
        session.close()
        vm.shutdown()

    # Destroy VM.
    if vm.is_alive():
        vm.destroy(gracefully=False)

    # Back up xml file.
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    # Get device path.
    device_source_path = ""
    if source_path:
        device_source_path = test.virtdir

    # Prepare test environment.
    qemu_config = LibvirtQemuConfig()
    if test_disks_format:
        qemu_config.allow_disk_format_probing = True
        utils_libvirtd.libvirtd_restart()

    # Create virtual device file.
    disks = []
    try:
        for i in range(len(device_source_names)):
            if test_disk_type_dir:
                # If we testing disk type dir option,
                # it needn't to create disk image
                disks.append({"format": "dir",
                              "source": device_source_names[i]})
            else:
                path = "%s/%s.%s" % (device_source_path,
                                     device_source_names[i], device_formats[i])
                disk = prepare_disk(path, device_formats[i])
                if disk:
                    disks.append(disk)

    except Exception, e:
        logging.error(repr(e))
        for img in disks:
            if img.has_key("disk_dev"):
                if img["format"] == "nfs":
                    img["disk_dev"].cleanup()
            else:
                if img["format"] == "iscsi":
                    libvirt.setup_or_cleanup_iscsi(is_setup=False)
                if img["format"] not in ["dir", "scsi"]:
                    os.remove(img["source"])
        raise exceptions.TestSkipError("Creating disk failed")

    # Build disks xml.
    disks_xml = []
    # Additional disk images.
    disks_img = []
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    try:
        for i in range(len(disks)):
            disk_xml = Disk(type_name=device_types[i])
            # If we are testing image file on iscsi disk,
            # mount the disk and then create the image.
            if test_file_img_on_disk:
                mount_path = "/tmp/diskimg"
                if process.run("mkdir -p %s && mount %s %s"
                               % (mount_path, disks[i]["source"],
                                  mount_path), ignore_status=True,
                               shell=True).exit_status:
                    raise exceptions.TestSkipError("Prepare disk failed")
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
                disk_xml.source = disk_xml.new_disk_source(
                    **{"attrs": source_dict})

            if len(device_bootorder) > i:
                disk_xml.boot = device_bootorder[i]

            if test_block_size:
                disk_xml.blockio = {"logical_block_size": logical_block_size,
                                    "physical_block_size": physical_block_size}

            if wwn != "":
                disk_xml.wwn = wwn
            if serial != "":
                disk_xml.serial = serial
            if vendor != "":
                disk_xml.vendor = vendor
            if product != "":
                disk_xml.product = product

            disk_xml.target = {"dev": device_targets[i], "bus": device_bus[i]}
            if len(device_readonly) > i:
                disk_xml.readonly = "yes" == device_readonly[i]

            # Add driver options from parameters
            driver_dict = {"name": "qemu"}
            if len(driver_options) > i:
                for driver_option in driver_options[i].split(','):
                    if driver_option != "":
                        d = driver_option.split('=')
                        driver_dict.update({d[0].strip(): d[1].strip()})
            disk_xml.driver = driver_dict

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
                    osxml.bios_reboot_timeout = "-1"

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

            disk.target = {"dev": bootdisk_target, "bus": bootdisk_bus}
            device_source = disk.source.attrs["file"]

            del disk.address
            vmxml.devices = xml_devices
            vmxml.define()

        # Add virtio_scsi controller.
        if virtio_scsi_controller:
            scsi_controller = Controller("controller")
            scsi_controller.type = "scsi"
            scsi_controller.index = "0"
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
            vmxml.del_controller("scsi")
            vmxml.add_device(scsi_controller)

        # Test usb devices.
        usb_devices = {}
        if add_usb_device:
            # Delete all usb devices first.
            controllers = vmxml.get_devices(device_type="controller")
            for ctrl in controllers:
                if ctrl.type == "usb":
                    vmxml.del_device(ctrl)

            inputs = vmxml.get_devices(device_type="input")
            for input in inputs:
                if input.type_name == "tablet":
                    vmxml.del_device(input)

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
            addr_dict = {}
            if input_usb_address != "":
                for addr_option in input_usb_address.split(','):
                    if addr_option != "":
                        d = addr_option.split('=')
                        addr_dict.update({d[0].strip(): d[1].strip()})
            if addr_dict:
                input_obj.address = input_obj.new_input_address(
                    **{"attrs": addr_dict})
            vmxml.add_device(input_obj)
            usb_devices.update({"input": addr_dict})

            hub_obj = Hub("usb")
            addr_dict = {}
            if hub_usb_address != "":
                for addr_option in hub_usb_address.split(','):
                    if addr_option != "":
                        d = addr_option.split('=')
                        addr_dict.update({d[0].strip(): d[1].strip()})
            if addr_dict:
                hub_obj.address = hub_obj.new_hub_address(
                    **{"attrs": addr_dict})
            vmxml.add_device(hub_obj)
            usb_devices.update({"hub": addr_dict})

        if dom_iothreads:
            # Delete cputune/iothreadids section, it may have conflict
            # with domain iothreads
            del vmxml.cputune
            del vmxml.iothreadids
            vmxml.iothreads = int(dom_iothreads)

        # After compose the disk xml, redefine the VM xml.
        vmxml.sync()

        # Test snapshot before vm start.
        if test_disk_snapshot:
            if snapshot_before_start:
                ret = virsh.snapshot_create_as(vm_name, "s1 %s"
                                               % snapshot_option)
                libvirt.check_exit_status(ret, snapshot_error)

        # Start the VM.
        vm.start()
        if status_error:
            raise exceptions.TestFail("VM started unexpectedly")

        # Hotplug the disks.
        if device_at_dt_disk:
            for i in range(len(disks)):
                attach_option = ""
                if len(device_attach_option) > i:
                    attach_option = device_attach_option[i]
                ret = virsh.attach_disk(vm_name, disks[i]["source"],
                                        device_targets[i],
                                        attach_option)
                libvirt.check_exit_status(ret)

        elif hotplug:
            for i in range(len(disks_xml)):
                disks_xml[i].xmltreefile.write()
                attach_option = ""
                if len(device_attach_option) > i:
                    attach_option = device_attach_option[i]
                ret = virsh.attach_device(vm_name, disks_xml[i].xml,
                                          flagstr=attach_option)
                attach_error = False
                if len(device_attach_error) > i:
                    attach_error = "yes" == device_attach_error[i]
                libvirt.check_exit_status(ret, attach_error)

    except virt_vm.VMStartError as details:
        if not status_error:
            raise exceptions.TestFail('VM failed to start:\n%s' % details)
    except xcepts.LibvirtXMLError as details:
        if not define_error:
            raise exceptions.TestFail(details)
    else:
        # VM is started, perform the tests.
        if test_slots_order:
            if not check_disk_order(device_targets):
                raise exceptions.TestFail("Disks slots order error in domain xml")

        if test_disks_format:
            if not check_disk_format(device_targets, device_formats):
                raise exceptions.TestFail("Disks type error in VM xml")

        if test_boot_console:
            # Check if disks bootorder is as expected.
            expected_order = params.get("expected_order").split(',')
            if not check_boot_console(expected_order):
                raise exceptions.TestFail("Test VM bootorder failed")

        if test_block_size:
            # Check disk block size in VM.
            if not check_vm_block_size(device_targets,
                                       logical_block_size, physical_block_size):
                raise exceptions.TestFail("Test disk block size in VM failed")

        if test_disk_option_cmd:
            # Check if disk options take affect in qemu commmand line.
            cmd = ("ps -ef | grep %s | grep -v grep " % vm_name)
            if test_with_boot_disk:
                d_target = bootdisk_target
            else:
                d_target = device_targets[0]
                if device_with_source:
                    cmd += (" | grep %s" %
                            (device_source_names[0].replace(',', ',,')))
            io = vm_xml.VMXML.get_disk_attr(vm_name, d_target, "driver", "io")
            if io:
                cmd += " | grep aio=%s" % io
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
                cmd += " | grep discard=%s" % discard
            copy_on_read = vm_xml.VMXML.get_disk_attr(vm_name, d_target,
                                                      "driver", "copy_on_read")
            if copy_on_read:
                cmd += " | grep copy-on-read=%s" % copy_on_read

            iothread = vm_xml.VMXML.get_disk_attr(vm_name, d_target,
                                                  "driver", "iothread")
            if iothread:
                cmd += " | grep iothread=iothread%s" % iothread

            if serial != "":
                cmd += " | grep serial=%s" % serial
            if wwn != "":
                cmd += " | grep -E \"wwn=(0x)?%s\"" % wwn
            if vendor != "":
                cmd += " | grep vendor=%s" % vendor
            if product != "":
                cmd += " | grep \"product=%s\"" % product

            num_queues = ""
            ioeventfd = ""
            if virtio_scsi_controller_driver != "":
                for driver_option in virtio_scsi_controller_driver.split(','):
                    if driver_option != "":
                        d = driver_option.split('=')
                        if d[0].strip() == "queues":
                            num_queues = d[1].strip()
                        elif d[0].strip() == "ioeventfd":
                            ioeventfd = d[1].strip()
            if num_queues != "":
                cmd += " | grep num_queues=%s" % num_queues
            if ioeventfd:
                cmd += " | grep ioeventfd=%s" % ioeventfd

            iface_event_idx = ""
            if iface_driver != "":
                for driver_option in iface_driver.split(','):
                    if driver_option != "":
                        d = driver_option.split('=')
                        if d[0].strip() == "event_idx":
                            iface_event_idx = d[1].strip()
            if iface_event_idx != "":
                cmd += " | grep virtio-net-pci,event_idx=%s" % iface_event_idx

            if process.run(cmd, ignore_status=True, shell=True).exit_status:
                raise exceptions.TestFail("Check disk driver option failed")

        if test_disk_snapshot:
            ret = virsh.snapshot_create_as(vm_name, "s1 %s" % snapshot_option)
            libvirt.check_exit_status(ret, snapshot_error)

        # Check the disk bootorder.
        if test_disk_bootorder:
            for i in range(len(device_targets)):
                if len(device_attach_error) > i:
                    if device_attach_error[i] == "yes":
                        continue
                if device_bootorder[i] != vm_xml.VMXML.get_disk_attr(
                        vm_name, device_targets[i], "boot", "order"):
                    raise exceptions.TestFail("Check bootorder failed")

        # Check disk bootorder with snapshot.
        if test_disk_bootorder_snapshot:
            disk_name = os.path.splitext(device_source)[0]
            check_bootorder_snapshot(disk_name)

        # Check disk readonly option.
        if test_disk_readonly:
            if not check_readonly(device_targets):
                raise exceptions.TestFail("Checking disk readonly option failed")

        # Check disk bus device option in qemu command line.
        if test_bus_device_option:
            cmd = ("ps -ef | grep %s | grep -v grep " % vm_name)
            dev_bus = int(vm_xml.VMXML.get_disk_attr(vm_name, device_targets[0],
                                                     "address", "bus"), 16)
            if device_bus[0] == "virtio":
                pci_slot = int(vm_xml.VMXML.get_disk_attr(vm_name, device_targets[0],
                                                          "address", "slot"), 16)
                if devices[0] == "lun":
                    device_option = "scsi=on"
                else:
                    device_option = "scsi=off"
                cmd += (" | grep virtio-blk-pci,%s,bus=pci.%x,addr=0x%x"
                        % (device_option, dev_bus, pci_slot))
            if device_bus[0] in ["ide", "sata", "scsi"]:
                dev_unit = int(vm_xml.VMXML.get_disk_attr(vm_name, device_targets[0],
                                                          "address", "unit"), 16)
                dev_id = vm_xml.VMXML.get_disk_attr(vm_name, device_targets[0],
                                                    "alias", "name")
            if device_bus[0] == "ide":
                check_cmd = "/usr/libexec/qemu-kvm -device ? 2>&1 |grep -E 'ide-cd|ide-hd'"
                if process.run(check_cmd, ignore_status=True,
                               shell=True).exit_status:
                    raise exceptions.TestSkipError("ide-cd/ide-hd not supported"
                                                   " by this qemu-kvm")

                if devices[0] == "cdrom":
                    device_option = "ide-cd"
                else:
                    device_option = "ide-hd"
                cmd += (" | grep %s,bus=ide.%d,unit=%d,drive=drive-%s,id=%s"
                        % (device_option, dev_bus, dev_unit, dev_id, dev_id))
            if device_bus[0] == "sata":
                cmd += (" | grep 'device ahci,.*,bus=pci.%s'" % dev_bus)
            if device_bus[0] == "scsi":
                if devices[0] == "lun":
                    device_option = "scsi-block"
                elif devices[0] == "cdrom":
                    device_option = "scsi-cd"
                else:
                    device_option = "scsi-hd"
                cmd += (" | grep %s,bus=scsi%d.%d,.*drive=drive-%s,id=%s"
                        % (device_option, dev_bus, dev_unit, dev_id, dev_id))
            if device_bus[0] == "usb":
                dev_port = vm_xml.VMXML.get_disk_attr(vm_name, device_targets[0],
                                                      "address", "port")
                dev_id = vm_xml.VMXML.get_disk_attr(vm_name, device_targets[0],
                                                    "alias", "name")
                if devices[0] == "disk":
                    cmd += (" | grep usb-storage,bus=usb%s.0,port=%s,"
                            "drive=drive-%s,id=%s"
                            % (dev_bus, dev_port, dev_id, dev_id))
                if usb_devices.has_key("input"):
                    cmd += (" | grep usb-tablet,id=input[0-9],bus=usb.%s,"
                            "port=%s" % (usb_devices["input"]["bus"],
                                         usb_devices["input"]["port"]))
                if usb_devices.has_key("hub"):
                    cmd += (" | grep usb-hub,id=hub0,bus=usb.%s,"
                            "port=%s" % (usb_devices["hub"]["bus"],
                                         usb_devices["hub"]["port"]))

            if process.run(cmd, ignore_status=True, shell=True).exit_status:
                raise exceptions.TestFail("Cann't see disk option"
                                          " in command line")

        if dom_iothreads:
            check_dom_iothread()

        # Check in VM after command.
        if check_patitions:
            if not check_vm_partitions(devices,
                                       device_targets):
                raise exceptions.TestFail("Cann't see device in VM")

        # Check disk save and restore.
        if test_disk_save_restore:
            save_file = "/tmp/%s.save" % vm_name
            check_disk_save_restore(save_file, device_targets,
                                    startup_policy)
            if os.path.exists(save_file):
                os.remove(save_file)

        # If we testing hotplug, detach the disk at last.
        if device_at_dt_disk:
            for i in range(len(disks)):
                dt_options = ""
                if devices[i] == "cdrom":
                    dt_options = "--config"
                ret = virsh.detach_disk(vm_name, device_targets[i],
                                        dt_options, **virsh_dargs)
                libvirt.check_exit_status(ret)
            # Check disks in VM after hotunplug.
            if check_patitions_hotunplug:
                if not check_vm_partitions(devices,
                                           device_targets, False):
                    raise exceptions.TestFail("See device in VM after hotunplug")

        elif hotplug:
            for i in range(len(disks_xml)):
                if len(device_attach_error) > i:
                    if device_attach_error[i] == "yes":
                        continue
                ret = virsh.detach_device(vm_name, disks_xml[i].xml,
                                          flagstr=attach_option, **virsh_dargs)
                os.remove(disks_xml[i].xml)
                libvirt.check_exit_status(ret)

            # Check disks in VM after hotunplug.
            if check_patitions_hotunplug:
                if not check_vm_partitions(devices,
                                           device_targets, False):
                    raise exceptions.TestFail("See device in VM after hotunplug")

    finally:
        # Delete snapshots.
        libvirt.clean_up_snapshots(vm_name, domxml=vmxml_backup)

        # Recover VM.
        if vm.is_alive():
            vm.destroy(gracefully=False)
        vmxml_backup.sync("--snapshots-metadata")

        # Restore qemu_config file.
        qemu_config.restore()
        utils_libvirtd.libvirtd_restart()

        for img in disks_img:
            os.remove(img["source"])
            if os.path.exists(img["path"]):
                process.run("umount %s && rmdir %s"
                            % (img["path"], img["path"]), ignore_status=True,
                            shell=True)

        for img in disks:
            if img.has_key("disk_dev"):
                if img["format"] == "nfs":
                    img["disk_dev"].cleanup()

                del img["disk_dev"]
            else:
                if img["format"] == "scsi":
                    libvirt.delete_scsi_disk()
                elif img["format"] == "iscsi":
                    libvirt.setup_or_cleanup_iscsi(is_setup=False)
                elif img["format"] not in ["dir"]:
                    if os.path.exists(img["source"]):
                        os.remove(img["source"])
