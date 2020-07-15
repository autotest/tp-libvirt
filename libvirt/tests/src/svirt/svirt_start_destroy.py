import os
import logging
import re

from virttest import utils_selinux
from virttest import virt_vm
from virttest import utils_config
from virttest import utils_libvirtd
from virttest import utils_test
from virttest import virsh
from virttest import libvirt_version
from virttest.libvirt_xml.vm_xml import VMXML

from avocado.utils import process


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
    xattr_check = 'yes' == params.get("xattr_check", 'no')
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
    for disk in list(disks.values()):
        disk_path = disk['source']
        backup_labels_of_disks[disk_path] = utils_selinux.get_context_of_file(
            filename=disk_path)
        stat_re = os.stat(disk_path)
        backup_ownership_of_disks[disk_path] = "%s:%s" % (stat_re.st_uid,
                                                          stat_re.st_gid)
    # Backup selinux of host.
    backup_sestatus = utils_selinux.get_status()

    qemu_conf = utils_config.LibvirtQemuConfig()
    libvirtd = utils_libvirtd.Libvirtd()

    def _resolve_label(label_string):
        labels = label_string.split(":")
        label_type = labels[2]
        if len(labels) == 4:
            label_range = labels[3]
        elif len(labels) > 4:
            label_range = "%s:%s" % (labels[3], labels[4])
        else:
            label_range = None
        return (label_type, label_range)

    def _check_label_equal(label1, label2):
        label1s = label1.split(":")
        label2s = label2.split(":")
        for i in range(len(label1s)):
            if label1s[i] != label2s[i]:
                return False
        return True

    try:
        # Set disk label
        (img_label_type, img_label_range) = _resolve_label(img_label)
        for disk in list(disks.values()):
            disk_path = disk['source']
            dir_path = "%s(/.*)?" % os.path.dirname(disk_path)
            try:
                utils_selinux.del_defcon(img_label_type, pathregex=dir_path)
            except Exception as err:
                logging.debug("Delete label failed: %s", err)
            # Using semanage set context persistently
            utils_selinux.set_defcon(context_type=img_label_type,
                                     pathregex=dir_path,
                                     context_range=img_label_range)
            utils_selinux.verify_defcon(pathname=disk_path,
                                        readonly=False,
                                        forcedesc=True)
            if sec_relabel == "no" and sec_type == 'none':
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

        # restart libvirtd
        libvirtd.restart()

        # Start VM to check the VM is able to access the image or not.
        try:
            # Need another guest to test the xattr added by libvirt
            if xattr_check:
                blklist = virsh.domblklist(vm_name, debug=True)
                target_disk = re.findall(r"[v,s]d[a-z]", blklist.stdout.strip())[0]
                guest_name = "%s_%s" % (vm_name, '1')
                cmd = "virt-clone --original %s --name %s " % (vm_name, guest_name)
                cmd += "--auto-clone --skip-copy=%s" % target_disk
                process.run(cmd, shell=True, verbose=True)
            vm.start()
            # Start VM successfully.
            # VM with seclabel can access the image with the context.
            if status_error:
                test.fail("Test succeeded in negative case.")
            # Start another vm with the same disk image.
            # The xattr will not be changed.
            if xattr_check:
                virsh.start(guest_name, ignore_status=True, debug=True)
            # Check the label of VM and image when VM is running.
            vm_context = utils_selinux.get_context_of_process(vm.get_pid())
            if (sec_type == "static") and (not vm_context == sec_label):
                test.fail("Label of VM is not expected after "
                          "starting.\n"
                          "Detail: vm_context=%s, sec_label=%s"
                          % (vm_context, sec_label))
            disk_context = utils_selinux.get_context_of_file(
                filename=list(disks.values())[0]['source'])
            if (sec_relabel == "no") and (not disk_context == img_label):
                test.fail("Label of disk is not expected after VM "
                          "starting.\n"
                          "Detail: disk_context=%s, img_label=%s."
                          % (disk_context, img_label))
            if sec_relabel == "yes" and not no_sec_model:
                vmxml = VMXML.new_from_dumpxml(vm_name)
                imagelabel = vmxml.get_seclabel()[0]['imagelabel']
                # the disk context is 'system_u:object_r:svirt_image_t:s0',
                # when VM started, the MLS/MCS Range will be added automatically.
                # imagelabel turns to be 'system_u:object_r:svirt_image_t:s0:cxx,cxxx'
                # but we shouldn't check the MCS range.
                if not _check_label_equal(disk_context, imagelabel):
                    test.fail("Label of disk is not relabeled by "
                              "VM\nDetal: disk_context="
                              "%s, imagelabel=%s"
                              % (disk_context, imagelabel))
                expected_results = "trusted.libvirt.security.ref_dac=\"1\"\n"
                expected_results += "trusted.libvirt.security.ref_selinux=\"1\""
                cmd = "getfattr -m trusted.libvirt.security -d %s " % list(disks.values())[0]['source']
                utils_test.libvirt.check_cmd_output(cmd, content=expected_results)

            # Check the label of disk after VM being destroyed.
            if poweroff_with_destroy:
                vm.destroy(gracefully=False)
            else:
                vm.wait_for_login()
                vm.shutdown()
            filename = list(disks.values())[0]['source']
            img_label_after = utils_selinux.get_context_of_file(filename)
            stat_re = os.stat(filename)
            ownership_of_disk = "%s:%s" % (stat_re.st_uid, stat_re.st_gid)
            logging.debug("The ownership of disk after guest starting is:\n")
            logging.debug(ownership_of_disk)
            logging.debug("The ownership of disk before guest starting is:\n")
            logging.debug(backup_ownership_of_disks[filename])
            if not (sec_relabel == "no" and sec_type == 'none'):
                if not libvirt_version.version_compare(5, 6, 0):
                    if img_label_after != img_label:
                        # Bug 547546 - RFE: the security drivers must remember original
                        # permissions/labels and restore them after
                        # https://bugzilla.redhat.com/show_bug.cgi?id=547546
                        err_msg = "Label of disk is not restored in VM shuting down.\n"
                        err_msg += "Detail: img_label_after=%s, " % img_label_after
                        err_msg += "img_label_before=%s.\n" % img_label
                        err_msg += "More info in https://bugzilla.redhat.com/show_bug"
                        err_msg += ".cgi?id=547546"
                        test.fail(err_msg)
                elif (img_label_after != img_label or
                      ownership_of_disk != backup_ownership_of_disks[filename]):
                    err_msg = "Label of disk is not restored in VM shuting down.\n"
                    err_msg += "Detail: img_label_after=%s, %s " % (img_label_after, ownership_of_disk)
                    err_msg += "img_label_before=%s, %s\n" % (img_label, backup_ownership_of_disks[filename])
                    test.fail(err_msg)
            # The xattr should be cleaned after guest shutoff.
            cmd = "getfattr -m trusted.libvirt.security -d %s " % list(disks.values())[0]['source']
            utils_test.libvirt.check_cmd_output(cmd, content="")
        except virt_vm.VMStartError as e:
            # Starting VM failed.
            # VM with seclabel can not access the image with the context.
            if not status_error:
                test.fail("Test failed in positive case."
                          "error: %s" % e)
    finally:
        # clean up
        for path, label in list(backup_labels_of_disks.items()):
            # Using semanage set context persistently
            dir_path = "%s(/.*)?" % os.path.dirname(path)
            (img_label_type, img_label_range) = _resolve_label(label)
            try:
                utils_selinux.del_defcon(img_label_type, pathregex=dir_path)
            except Exception as err:
                logging.debug("Delete label failed: %s", err)
            utils_selinux.set_defcon(context_type=img_label_type,
                                     pathregex=dir_path,
                                     context_range=img_label_range)
            utils_selinux.verify_defcon(pathname=path,
                                        readonly=False,
                                        forcedesc=True)
        for path, label in list(backup_ownership_of_disks.items()):
            label_list = label.split(":")
            os.chown(path, int(label_list[0]), int(label_list[1]))
        backup_xml.sync()
        if xattr_check:
            virsh.undefine(guest_name, ignore_status=True)
        utils_selinux.set_status(backup_sestatus)
        if (security_driver or security_default_confined or
                security_require_confined):
            qemu_conf.restore()
            libvirtd.restart()
