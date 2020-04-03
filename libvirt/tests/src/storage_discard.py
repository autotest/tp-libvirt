"""
Test module for Storage Discard.
"""

import re
import logging
import time

from avocado.utils import lv_utils
from avocado.utils import process
from avocado.core import exceptions

from virttest import virsh
from virttest import data_dir
from virttest import iscsi
from virttest import qemu_storage
from virttest import libvirt_vm
from virttest import utils_misc
from virttest.utils_test import libvirt as utlv


def volumes_capacity(lv_name):
    """
    Get volume's information about its capacity percentage.
    """
    volumes = lv_utils.lv_list()
    if lv_name in list(volumes.keys()):
        return volumes[lv_name]["Origin_Data"]
    return None


def get_disk_capacity(test, disk_type, imagefile=None, lvname=None):
    """
    Get disk capacity space on host.
    Take attention: for file image, it's Metabytes
                    while for block, it's volume space percentage in 'lvs'
    """
    if disk_type == "file":
        result = process.run("du -sm %s" % imagefile, shell=True)
        return result.stdout_text.split()[0].strip()
    elif disk_type == "block":
        if lvname is None:
            test.fail("No volume name provided.")
        lv_cap = volumes_capacity(lvname)
        if lv_cap is None:
            test.fail("Get volume capacity failed.")
        return lv_cap


def create_iscsi_device(test, device_size="2G"):
    """
    Create a iscsi device.
    """
    imgname = "emulated_iscsi"
    device_name = utlv.setup_or_cleanup_iscsi(is_setup=True,
                                              emulated_image=imgname,
                                              image_size=device_size)
    # Verify if expected iscsi device has been set
    for iscsi_node in iscsi.iscsi_get_sessions():
        if iscsi_node[1].count(imgname):
            # Remove port for pool operations
            ip_addr = iscsi_node[0].split(":3260")[0]
            iscsi_device = (ip_addr, iscsi_node[1])
            break
    if iscsi_device == ():
        test.fail("No matched iscsi device.")

    check_ret = process.run("ls %s" % device_name, shell=True)
    if check_ret.exit_status:
        test.fail("Can not find provided device:%s" % check_ret)
    return device_name


def create_volume(device, vgname="vgthin", lvname="lvthin"):
    """
    Create volume through provided device or created by
    iscsi service if it is None.
    """
    # Create volume group
    lv_utils.vg_create(vgname, device)
    # Create thin volume
    thinpool, thinlv = lv_utils.thin_lv_create(vgname, thinlv_name=lvname)
    logging.debug("Created thin volume successfully.")
    return "/dev/%s/%s" % (vgname, lvname)


def create_disk_xml(disk_type, device_path, discard_type='ignore',
                    target_dev='sdb', target_bus='scsi'):
    """
    Create a XML contains disk information for virsh attach-device command.
    """
    disk_params = {'type_name': disk_type, 'device': "disk",
                   'driver_name': "qemu",
                   'driver_type': "raw", 'driver_discard': discard_type,
                   'target_dev': target_dev, 'target_bus': target_bus,
                   'source_file': device_path}
    return utlv.create_disk_xml(disk_params)


def prepare_channel_xml(vm_name, agent_index=0):
    """
    Create a XML contains channel information for agent.
    """
    channel_path = ("/var/lib/libvirt/qemu/channel/target/"
                    "%s.org.qemu.guest_agent.%s" % (vm_name, agent_index))
    channel_params = {'channel_type_name': 'unix',
                      'source_mode': 'bind',
                      'source_path': channel_path,
                      'target_type': 'virtio',
                      'target_name': "org.qemu.guest_agent.%s" % agent_index}
    channel_xml = utlv.create_channel_xml(channel_params)
    return channel_xml.xml


def get_vm_disks(vm):
    """
    Get disks list in vm.
    """
    session = vm.wait_for_login()
    cmd = "fdisk -l|grep \"^Disk /dev\""
    output = session.cmd_output(cmd).strip()
    session.close()
    disks = []
    for line in output.splitlines():
        disks.append(line.split(":")[0].split()[-1])
    return disks


def _sync_finished():
    """
    check volume's capacity once every 60 seconds
    """
    lv_cap_before = volumes_capacity("lvthin")
    time.sleep(60)
    lv_cap_later = volumes_capacity("lvthin")
    logging.debug("Synchronizing: %s -> %s", lv_cap_before, lv_cap_later)
    return lv_cap_before == lv_cap_later


def occupy_disk(vm, device, size, frmt_type="ext4", mount_options=None):
    """
    Create an image file in formatted device.

    :param size: the count in Metabytes
    """
    session = vm.wait_for_login()
    try:
        session.cmd("mkfs -F -t %s %s" % (frmt_type, device), timeout=120)
        if mount_options is not None:
            mount_cmd = "mount -o %s %s /mnt" % (mount_options, device)
        else:
            mount_cmd = "mount %s /mnt" % device
        session.cmd(mount_cmd)
        dd_cmd = "dd if=/dev/zero of=/mnt/test.img bs=1M count=%s" % size
        session.cmd(dd_cmd, timeout=120)
        utils_misc.wait_for(_sync_finished, timeout=300)
        # Delete image to create sparsing space
        session.cmd("rm -f /mnt/test.img")
    finally:
        session.close()


def sig_delta(size1, size2, tolerable_shift=0.8):
    """
    To verfiy whether two size have significant shift.
    """
    s1 = int(float(size1))
    s2 = int(float(size2))
    if int(s2) == 0:
        s2 += 1
    if int(s1) == 0:
        s1 += 1
    return ((abs(s1 - s2) / s2) > tolerable_shift)


def do_fstrim(test, fstrim_type, vm, status_error=False):
    """
    Execute fstrim in different ways, and check its result.
    """
    # ensure that volume capacity won't change before trimming disk.
    if not _sync_finished():
        utils_misc.wait_for(_sync_finished, timeout=300)
    if fstrim_type == "fstrim_cmd":
        session = vm.wait_for_login()
        output = session.cmd_output("fstrim -v /mnt", timeout=240)
        session.close()
        logging.debug(output)
        if re.search("Operation not supported", output):
            if status_error:
                # virtio is not supported in unmap operations
                logging.debug("Expected failure: virtio do not support fstrim")
                return
            else:
                test.fail("Not supported fstrim on supported "
                          "envrionment.Bug?")
        try:
            trimmed_bytes = re.search("\d+\sbytes",
                                      output).group(0).split()[0]
            trimmed = int(trimmed_bytes)
            logging.debug("Trimmed size is:%s bytes", trimmed)
        except (AttributeError, IndexError) as detail:
            test.fail("Do fstrim failed:%s" % detail)
        if trimmed == 0:
            test.fail("Trimmed size is 0.")
    elif fstrim_type == "mount_with_discard":
        pass
    elif fstrim_type == "qemu-guest-agent":
        cmd = ("qemu-agent-command %s '{\"execute\":\"guest-fstrim\"}'"
               % vm.name)
        try:
            virsh.command(cmd, debug=True, ignore_status=False)
        except process.CmdError:
            test.fail("Execute qemu-agent-command failed.")


def run(test, params, env):
    """
    DiskXML has an attribute named discard for fstrim operations.
    (Only supported after special libvirt version.)
    These are test cases for it:
    """
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    if vm.is_dead():
        vm.start()
        vm.wait_for_login()
    bf_disks = get_vm_disks(vm)
    vm.destroy()

    # Create a new vm for test, undefine it at last
    new_vm_name = "%s_discardtest" % vm.name
    if not utlv.define_new_vm(vm.name, new_vm_name):
        test.error("Define new vm failed.")
    try:
        new_vm = libvirt_vm.VM(new_vm_name, vm.params, vm.root_dir,
                               vm.address_cache)
    except Exception as detail:
        test.error("Create new vm failed:%s" % detail)

    disk_type = params.get("disk_type", "file")
    discard_device = params.get("discard_device", "/DEV/EXAMPLE")
    fstrim_type = params.get("fstrim_type", "fstrim_cmd")
    try:
        if disk_type == "file":
            device_dir = data_dir.get_tmp_dir()
            params["image_name"] = "discard_test"
            params["image_format"] = "raw"
            params["image_size"] = "1G"
            qs = qemu_storage.QemuImg(params, device_dir, "")
            device_path, _ = qs.create(params)
        else:
            if not discard_device.count("/DEV/EXAMPLE"):
                create_iscsi = False
            else:
                create_iscsi = True
                discard_device = create_iscsi_device(test)
            device_path = create_volume(discard_device)

        discard_type = params.get("discard_type", "ignore")
        target_bus = params.get("storage_target_bus", "virtio")
        target_dev = params.get("storage_target_dev", "vdb")
        status_error = "yes" == params.get("status_error", "no")
        xmlfile = create_disk_xml(disk_type, device_path, discard_type,
                                  target_dev, target_bus)
        virsh.attach_device(new_vm_name, xmlfile,
                            flagstr="--persistent", ignore_status=False)
        if fstrim_type == "qemu-guest-agent":
            channelfile = prepare_channel_xml(new_vm_name)
            virsh.attach_device(new_vm_name, channelfile,
                                flagstr="--persistent", ignore_status=False)
        logging.debug("New VMXML:\n%s", virsh.dumpxml(new_vm_name))

        # Verify attached device in vm
        if new_vm.is_dead():
            new_vm.start()
        new_vm.wait_for_login()
        af_disks = get_vm_disks(new_vm)
        logging.debug("\nBefore:%s\nAfter:%s", bf_disks, af_disks)
        # Get new disk name in vm
        new_disk = "".join(list(set(bf_disks) ^ set(af_disks)))
        if not new_disk:
            test.fail("Can not get attached device in vm.")
        logging.debug("Attached device in vm:%s", new_disk)

        # Occupt space of new disk
        frmt_type = params.get("discard_format", "ext4")
        if fstrim_type == "mount_with_discard":
            mount_options = "discard"
        else:
            mount_options = None

        bf_cpy = get_disk_capacity(test, disk_type, imagefile=device_path,
                                   lvname="lvthin")
        logging.debug("Disk size before using:%s", bf_cpy)
        occupy_disk(new_vm, new_disk, "500", frmt_type, mount_options)
        bf_fstrim_cpy = get_disk_capacity(test, disk_type, imagefile=device_path,
                                          lvname="lvthin")
        logging.debug("Disk size after used:%s", bf_fstrim_cpy)
        do_fstrim(test, fstrim_type, new_vm, status_error)
        af_fstrim_cpy = get_disk_capacity(test, disk_type, imagefile=device_path,
                                          lvname="lvthin")
        logging.debug("\nBefore occupying disk:%s\n"
                      "After occupied disk:%s\n"
                      "After fstrim operation:%s",
                      bf_cpy, bf_fstrim_cpy, af_fstrim_cpy)
        # Check results
        if fstrim_type in ["fstrim_cmd", "qemu-guest-agent"]:
            if not sig_delta(bf_fstrim_cpy, af_fstrim_cpy) and \
                    not status_error:
                test.fail("Manual 'fstrims' didn't work.")
        elif fstrim_type == "mount_with_discard":
            if sig_delta(bf_cpy, bf_fstrim_cpy) and not status_error:
                test.fail("Automatical 'fstrims' didn't work.")
    finally:
        if new_vm.is_alive():
            new_vm.destroy()
        new_vm.undefine()
        if disk_type == "block":
            try:
                lv_utils.lv_remove("vgthin", "lvthin")
            except exceptions.TestError as detail:
                logging.debug(str(detail))
            try:
                lv_utils.vg_remove("vgthin")
            except exceptions.TestError as detail:
                logging.debug(str(detail))
            process.run("pvremove -f %s" % discard_device, ignore_status=True,
                        shell=True)
            if create_iscsi:
                utlv.setup_or_cleanup_iscsi(is_setup=False)
