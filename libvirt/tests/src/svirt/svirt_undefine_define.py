"""
svirt guest_undefine_define test.
"""
import os

from virttest import utils_selinux
from virttest import virsh
from virttest import data_dir
from virttest.libvirt_xml.vm_xml import VMXML


def run(test, params, env):
    """
    Test svirt in adding disk to VM.

    (1).Init variables for test.
    (2).Label the VM and disk with proper label.
    (3).Start VM and check the context.
    (4).Destroy VM and check the context.
    """
    # Get general variables.
    status_error = ('yes' == params.get("status_error", 'no'))
    host_sestatus = params.get(
        "svirt_undefine_define_host_selinux", "enforcing")
    # Get variables about seclabel for VM.
    sec_type = params.get("svirt_undefine_define_vm_sec_type", "dynamic")
    sec_model = params.get("svirt_undefine_define_vm_sec_model", "selinux")
    sec_label = params.get("svirt_undefine_define_vm_sec_label", None)
    sec_relabel = params.get("svirt_undefine_define_vm_sec_relabel", "yes")
    sec_dict = {'type': sec_type, 'model': sec_model, 'label': sec_label,
                'relabel': sec_relabel}
    # Get variables about VM and get a VM object and VMXML instance.
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vmxml = VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()

    # Get varialbles about image.
    img_label = params.get('svirt_undefine_define_disk_label')
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

    try:
        xml_file = (os.path.join(data_dir.get_tmp_dir(), "vmxml"))
        if vm.is_alive():
            vm.destroy()
        virsh.dumpxml(vm.name, to_file=xml_file)
        cmd_result = virsh.undefine(vm.name)
        if cmd_result.exit_status:
            test.fail("Failed to undefine vm."
                      "Detail: %s" % cmd_result)
        cmd_result = virsh.define(xml_file)
        if cmd_result.exit_status:
            test.fail("Failed to define vm."
                      "Detail: %s" % cmd_result)
    finally:
        # clean up
        for path, label in list(backup_labels_of_disks.items()):
            utils_selinux.set_context_of_file(filename=path, context=label)
        backup_xml.sync()
        utils_selinux.set_status(backup_sestatus)
