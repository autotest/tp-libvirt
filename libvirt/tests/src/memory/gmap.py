import logging as log
import os
from pathlib import Path

from avocado.core.exceptions import TestError
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_misc import cmd_status_output


logging = log.getLogger('avocado.' + __name__)
cleanup_actions = []


def parent(file_path):
    """
    Returns the parent directory of a file path

    :param file_path: absolute file path
    :return path: absolute path of parent directory
    """
    return Path(file_path).parent.absolute()


def get_source_disk_path(vmxml):
    """
    Returns the absolute path to a disk image for import.
    Assume the boot image is the first disk and an image file.

    :param vmxml: VMXML instance
    :return: absolute path to the guest's first disk image file
    """
    disks = vmxml.get_disk_all()
    disk_list = list(disks.values())
    first_disk = disk_list[0]
    return first_disk.find('source').get('file')


def update_l2_vm(vmxml, l2_mem, fs_dict):
    """
    Update the L2 VM to run L3 inside from virtiofs

    :param vmxml: The VMXML instance
    :param l2_mem: The new memory size
    :param fs_dict: The filesystem device attributes
    """
    vmxml.memory = l2_mem
    mb = vm_xml.VMMemBackingXML()
    mb.access_mode = "shared"
    vmxml.mb = mb
    vmxml.sync()
    fs = libvirt_vmxml.create_vm_device_by_type("filesystem", fs_dict)
    source = fs["source"]
    source.update({'dir': parent(get_source_disk_path(vmxml))})
    fs["source"] = source
    virsh.attach_device(vmxml.vm_name, fs.xml, flagstr="--config", debug=True)


def prepare_l3(vmxml):
    """
    Prepares the disk for the L3 guest

    :param vmxml: The L2 VMXML instance
    """
    l2_disk_path = get_source_disk_path(vmxml)
    l3_disk_path = os.path.join(parent(l2_disk_path), "test.qcow2")
    cmd = ("qemu-img convert -f qcow2 -O qcow2 -o lazy_refcounts=on"
           " {} {}".format(l2_disk_path, l3_disk_path))
    cmd_status_output(cmd)
    cleanup_actions.append(lambda: os.unlink(l3_disk_path))


def start_l3(session, target_tag):
    """
    Mount the shared directory with the image file and start
    the L3 guest with the qemu-kvm command

    :param session: VM session
    :param target_tag: virtiofs mount tag
    """
    mount_dir = "/mnt"
    cmd = "mount -t virtiofs {} {}".format(target_tag, mount_dir)
    cmd_status_output(cmd, session=session)
    cmd = ("/usr/libexec/qemu-kvm -m 1024 -smp 1"
           " -daemonize {}".format(os.path.join(mount_dir, "test.qcow2")))
    s, _ = cmd_status_output(cmd, session=session)
    if s:
        raise TestError("Couldn't start L3 guest.")


def l1_counters_go_up():
    """
    Check that the shadow memory counters go up
    """
    base_path = "/sys/kernel/debug/kvm/"
    for a in [
            "gmap_shadow_reuse",
            "gmap_shadow_create",
            "gmap_shadow_sg_entry",
            "gmap_shadow_pg_entry"
            ]:
        with open(os.path.join(base_path, a), "r") as f:
            if f.read().strip() == "0":
                return False
    return True


def run(test, params, env):
    """
    Test kvm counters for L3 shadow memory
    L1: LPAR
    L2: KVM guest
    L3: nested KVM guest
    """
    vm_name = params.get('main_vm')
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    cleanup_actions.append(lambda: bkxml.sync())
    vm = env.get_vm(vm_name)
    l2_mem = int(params.get("l2_mem", "3906250"))
    target_tag = params.get("target_tag")
    fs_dict = eval(params.get("fs_dict"))

    try:
        update_l2_vm(vmxml, l2_mem, fs_dict)
        prepare_l3(vmxml)
        logging.debug("VMXML: %s", vm_xml.VMXML.new_from_dumpxml(vm_name))
        vm.start()
        cleanup_actions.append(lambda: vm.destroy())
        session = vm.wait_for_login()
        cleanup_actions.append(lambda: session.close())
        start_l3(session, target_tag)
        if not l1_counters_go_up():
            test.fail("Gmap counters didn't go up.")
    finally:
        cleanup_actions.reverse()
        for action in cleanup_actions:
            action()
