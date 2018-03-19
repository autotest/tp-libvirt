"""
svirt virt-clone test.
"""
from avocado.utils import path as utils_path
from avocado.utils import process

from virttest import utils_selinux
from virttest.utils_test import libvirt as utils_libvirt
from virttest import virsh
from virttest import libvirt_vm
from virttest.libvirt_xml.vm_xml import VMXML


def run(test, params, env):
    """
    Test svirt in virt-clone.
    """
    VIRT_CLONE = None
    try:
        VIRT_CLONE = utils_path.find_command("virt-clone")
    except utils_path.CmdNotFoundError:
        test.cancel("No virt-clone command found.")

    # Get general variables.
    status_error = ('yes' == params.get("status_error", 'no'))
    host_sestatus = params.get("svirt_virt_clone_host_selinux", "enforcing")
    # Get variables about seclabel for VM.
    sec_type = params.get("svirt_virt_clone_vm_sec_type", "dynamic")
    sec_model = params.get("svirt_virt_clone_vm_sec_model", "selinux")
    sec_label = params.get("svirt_virt_clone_vm_sec_label", None)
    sec_relabel = params.get("svirt_virt_clone_vm_sec_relabel", "yes")
    sec_dict = {'type': sec_type, 'model': sec_model, 'label': sec_label,
                'relabel': sec_relabel}
    # Get variables about VM and get a VM object and VMXML instance.
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vmxml = VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()

    # Get varialbles about image.
    img_label = params.get('svirt_virt_clone_disk_label')
    # Label the disks of VM with img_label.
    disks = vm.get_disk_devices()
    backup_labels_of_disks = {}
    for disk in list(disks.values()):
        disk_path = disk['source']
        backup_labels_of_disks[disk_path] = utils_selinux.get_context_of_file(
            filename=disk_path)
        utils_selinux.set_context_of_file(filename=disk_path,
                                          context=img_label)
    # Set selinux of host.
    backup_sestatus = utils_selinux.get_status()
    utils_selinux.set_status(host_sestatus)
    # Set the context of the VM.
    vmxml.set_seclabel([sec_dict])
    vmxml.sync()

    clone_name = ("%s-clone" % vm.name)
    try:
        cmd = ("%s --original %s --name %s --auto-clone" %
               (VIRT_CLONE, vm.name, clone_name))
        cmd_result = process.run(cmd, ignore_status=True, shell=True)
        utils_libvirt.check_exit_status(cmd_result, status_error)
    finally:
        # clean up
        for path, label in list(backup_labels_of_disks.items()):
            utils_selinux.set_context_of_file(filename=path, context=label)
        backup_xml.sync()
        utils_selinux.set_status(backup_sestatus)
        if not virsh.domstate(clone_name).exit_status:
            libvirt_vm.VM(clone_name, params, None, None).remove_with_storage()
