import os
import logging
from autotest.client.shared import error
from virttest import utils_selinux
from virttest import virt_vm
from virttest import utils_config
from virttest import utils_libvirtd
from virttest.libvirt_xml.vm_xml import VMXML


def run(test, params, env):
    """
    Test svirt in adding disk to VM.

    (1).Init variables for test.
    (2).Config qemu conf if need
    (3).Label the VM and disk with proper label.
    (4).Start VM and check the context.
    (5).Destroy VM and check the context.
    """
    # Get general variables.
    status_error = ('yes' == params.get("status_error", 'no'))
    host_sestatus = params.get("svirt_start_destroy_host_selinux", "enforcing")
    # Get variables about seclabel for VM.
    sec_type = params.get("svirt_start_destroy_vm_sec_type", "dynamic")
    sec_model = params.get("svirt_start_destroy_vm_sec_model", "selinux")
    sec_label = params.get("svirt_start_destroy_vm_sec_label", None)
    sec_baselabel = params.get("svirt_start_destroy_vm_sec_baselabel", None)
    security_driver = params.get("security_driver", None)
    security_default_confined = params.get("security_default_confined", None)
    security_require_confined = params.get("security_require_confined", None)
    no_sec_model = 'yes' == params.get("no_sec_model", 'no')
    sec_relabel = params.get("svirt_start_destroy_vm_sec_relabel", "yes")
    sec_dict = {'type': sec_type, 'relabel': sec_relabel}
    sec_dict_list = []

    def _set_sec_model(model):
        """
        Set sec_dict_list base on given sec model type
        """
        sec_dict_copy = sec_dict.copy()
        sec_dict_copy['model'] = model
        if sec_type != "none":
            if sec_type == "dynamic" and sec_baselabel:
                sec_dict_copy['baselabel'] = sec_baselabel
            else:
                sec_dict_copy['label'] = sec_label
        sec_dict_list.append(sec_dict_copy)

    if not no_sec_model:
        if "," in sec_model:
            sec_models = sec_model.split(",")
            for model in sec_models:
                _set_sec_model(model)
        else:
            _set_sec_model(sec_model)
    else:
        sec_dict_list.append(sec_dict)

    logging.debug("sec_dict_list is: %s" % sec_dict_list)
    poweroff_with_destroy = ("destroy" == params.get(
                             "svirt_start_destroy_vm_poweroff", "destroy"))
    # Get variables about VM and get a VM object and VMXML instance.
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vmxml = VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()

    # Get varialbles about image.
    img_label = params.get('svirt_start_destroy_disk_label')
    # Backup disk Labels.
    disks = vm.get_disk_devices()
    backup_labels_of_disks = {}
    backup_ownership_of_disks = {}
    for disk in disks.values():
        disk_path = disk['source']
        backup_labels_of_disks[disk_path] = utils_selinux.get_context_of_file(
            filename=disk_path)
        f = os.open(disk_path, 0)
        stat_re = os.fstat(f)
        backup_ownership_of_disks[disk_path] = "%s:%s" % (stat_re.st_uid,
                                                          stat_re.st_gid)
    # Backup selinux of host.
    backup_sestatus = utils_selinux.get_status()

    qemu_conf = utils_config.LibvirtQemuConfig()
    libvirtd = utils_libvirtd.Libvirtd()
    try:
        # Set disk label
        for disk in disks.values():
            disk_path = disk['source']
            utils_selinux.set_context_of_file(filename=disk_path,
                                              context=img_label)
            os.chown(disk_path, 107, 107)

        # Set selinux of host.
        utils_selinux.set_status(host_sestatus)

        # Set qemu conf
        if security_driver:
            qemu_conf.set_string('security_driver', security_driver)
        if security_default_confined:
            qemu_conf.security_default_confined = security_default_confined
        if security_require_confined:
            qemu_conf.security_require_confined = security_require_confined
        if (security_driver or security_default_confined or
                security_require_confined):
            logging.debug("the qemu.conf content is: %s" % qemu_conf)
            libvirtd.restart()

        # Set the context of the VM.
        vmxml.set_seclabel(sec_dict_list)
        vmxml.sync()
        logging.debug("the domain xml is: %s" % vmxml.xmltreefile)

        # Start VM to check the VM is able to access the image or not.
        try:
            vm.start()
            # Start VM successfully.
            # VM with seclabel can access the image with the context.
            if status_error:
                raise error.TestFail("Test succeeded in negative case.")
            # Check the label of VM and image when VM is running.
            vm_context = utils_selinux.get_context_of_process(vm.get_pid())
            if (sec_type == "static") and (not vm_context == sec_label):
                raise error.TestFail("Label of VM is not expected after "
                                     "starting.\n"
                                     "Detail: vm_context=%s, sec_label=%s"
                                     % (vm_context, sec_label))
            disk_context = utils_selinux.get_context_of_file(
                filename=disks.values()[0]['source'])
            if (sec_relabel == "no") and (not disk_context == img_label):
                raise error.TestFail("Label of disk is not expected after VM "
                                     "starting.\n"
                                     "Detail: disk_context=%s, img_label=%s."
                                     % (disk_context, img_label))
            if sec_relabel == "yes" and not no_sec_model:
                vmxml = VMXML.new_from_dumpxml(vm_name)
                imagelabel = vmxml.get_seclabel()[0]['imagelabel']
                if not disk_context == imagelabel:
                    raise error.TestFail("Label of disk is not relabeled by "
                                         "VM\nDetal: disk_context="
                                         "%s, imagelabel=%s"
                                         % (disk_context, imagelabel))
            # Check the label of disk after VM being destroyed.
            if poweroff_with_destroy:
                vm.destroy(gracefully=False)
            else:
                vm.wait_for_login()
                vm.shutdown()
            img_label_after = utils_selinux.get_context_of_file(
                filename=disks.values()[0]['source'])
            if (not img_label_after == img_label):
                # Bug 547546 - RFE: the security drivers must remember original
                # permissions/labels and restore them after
                # https://bugzilla.redhat.com/show_bug.cgi?id=547546
                err_msg = "Label of disk is not restored in VM shuting down.\n"
                err_msg += "Detail: img_label_after=%s, " % img_label_after
                err_msg += "img_label_before=%s.\n" % img_label
                err_msg += "More info in https://bugzilla.redhat.com/show_bug"
                err_msg += ".cgi?id=547546"
                raise error.TestFail(err_msg)
        except virt_vm.VMStartError, e:
            # Starting VM failed.
            # VM with seclabel can not access the image with the context.
            if not status_error:
                raise error.TestFail("Test failed in positive case."
                                     "error: %s" % e)
    finally:
        # clean up
        for path, label in backup_labels_of_disks.items():
            utils_selinux.set_context_of_file(filename=path, context=label)
        for path, label in backup_ownership_of_disks.items():
            label_list = label.split(":")
            os.chown(path, int(label_list[0]), int(label_list[1]))
        backup_xml.sync()
        utils_selinux.set_status(backup_sestatus)
        if (security_driver or security_default_confined or
                security_require_confined):
            qemu_conf.restore()
            libvirtd.restart()
