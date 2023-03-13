import os
import pwd
import re

from avocado.utils import process

from virttest import virsh

from virttest.libvirt_xml.vm_xml import VMXML
from virttest.utils_libvirt import libvirt_disk
from virttest.utils_test import libvirt


def update_qemu_conf(vmxml, qemu_conf, seclabel_relabel, qemu_conf_user_group,
                     qemu_conf_user, qemu_conf_group=None):
    """
    Update qemu conf settings.

    :param vmxml: The vmxml object
    :param qemu_conf: The original qemu_conf setting
    :param seclabel_attr: seclabel settings
    :param qemu_conf_user_group: Whether set user/group in qemu_conf
    :param qemu_conf_user: User setting in qemu_conf
    :param qemu_conf_group: Group setting in qemu_conf, defaults to None
    :return: The updated qemu_conf dict
    """
    if qemu_conf_user_group:
        qemu_conf.update({"user": "\"%s\"" % qemu_conf_user})
        if qemu_conf_group:
            qemu_conf.update({"group": "\"%s\"" % qemu_conf_group})
        if vmxml.devices.by_device_tag('tpm'):
            qemu_conf.update({"swtpm_user": "\"%s\"" % qemu_conf_user,
                              "swtpm_group": "\"%s\"" % qemu_conf_group})
    elif (vmxml.devices.by_device_tag('tpm') and
          (qemu_conf.get("dynamic_ownership") == "0" or not seclabel_relabel)):
        qemu_conf.update({"user": "\"qemu\"", "group": "\"qemu\"",
                          "swtpm_user": "\"qemu\"", "swtpm_group": "\"qemu\""})
    return qemu_conf


def create_user(user):
    """
    Create user.

    :param user: User to create
    :return: User name if created
    """

    added_user = None
    try:
        usr = abs(int(user))
    except ValueError:
        usr = user
    else:
        usr = pwd.getpwuid(usr).pw_name
    if usr not in [x.pw_name for x in pwd.getpwall()]:
        process.run("useradd %s" % usr, ignore_status=False, shell=True)
        added_user = usr
    return added_user


def set_tpm_perms(vmxml, params):
    """
    Set the perms of swtpm lib to allow other users to write in the dir.

    :param vmxml: The vmxml object
    :param params: Dictionary with the test parameters
    """
    swtpm_lib = params.get("swtpm_lib")
    swtpm_perms_file = params.get("swtpm_perms_file")
    if vmxml.devices.by_device_tag('tpm'):
        cmd = "getfacl -R %s > %s" % (swtpm_lib, swtpm_perms_file)
        process.run(cmd, ignore_status=True, shell=True)
        cmd = "chmod -R 777 %s" % swtpm_lib
        process.run(cmd, ignore_status=False, shell=True)


def restore_tpm_perms(vmxml, params):
    """
    Restore the perms of swtpm lib.

    :param vmxml: The vmxml object
    :param params: Dictionary with the test parameters
    """
    swtpm_perms_file = params.get("swtpm_perms_file")
    if vmxml.devices.by_device_tag('tpm'):
        if os.path.isfile(swtpm_perms_file):
            cmd = "setfacl --restore=%s" % swtpm_perms_file
            process.run(cmd, ignore_status=True, shell=True)
            os.unlink(swtpm_perms_file)


def run(test, params, env):
    """
    Test overall domain dac <seclabel> can work correctly.

    1. Set VM xml and qemu.conf with proper DAC label, also set image and
        monitor socket parent dir with propoer ownership and mode.
    2. Start VM and check the context.
    3. Destroy VM and check the context.
    """

    # Get general variables.
    chown_img = params.get("chown_img")
    qemu_conf = eval(params.get("qemu_conf", "{}"))
    qemu_conf_user_group = "yes" == params.get("qemu_conf_user_group")
    xattr_selinux_str = params.get("xattr_selinux_str",
                                   "trusted.libvirt.security.ref_selinux=\"1\"")
    xattr_dac_str = params.get("xattr_dac_str", "security.ref_dac=\"1\"")
    backup_labels_of_disks = {}
    added_user = None
    qemu_conf_obj = None
    qemu_conf_user = None
    qemu_conf_group = None

    # Get variables about VM and get a VM object and VMXML instance.
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vmxml = VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()

    if not vmxml.devices.by_device_tag('tpm'):
        if "without_qemu_conf_user_group.0_107" in params.get("shortname"):
            params["status_error"] = "no"
    status_error = 'yes' == params.get("status_error", 'no')

    seclabel_attr = {k.replace('seclabel_attr_', ''): int(v) if v.isdigit()
                     else v for k, v in params.items()
                     if k.startswith('seclabel_attr_')}
    seclabel_relabel = seclabel_attr.get("relabel") == "yes"

    try:
        set_tpm_perms(vmxml, params)
        if seclabel_attr.get("label"):
            qemu_conf_user = seclabel_attr.get("label").split(":")[0]
            if len(seclabel_attr.get("label").split(":")) >= 2:
                qemu_conf_group = seclabel_attr.get("label").split(":")[1]
            if not status_error:
                added_user = create_user(qemu_conf_user)

        test.log.info("TEST_STEP: Update qemu.conf.")
        qemu_conf = update_qemu_conf(vmxml, qemu_conf, seclabel_relabel,
                                     qemu_conf_user_group, qemu_conf_user,
                                     qemu_conf_group)
        qemu_conf_obj = libvirt.customize_libvirt_config(qemu_conf, "qemu")

        if chown_img:
            qemu_info = pwd.getpwnam(chown_img.split(":")[0])
            uid, gid = qemu_info.pw_uid, qemu_info.pw_gid
            for disk in list(vm.get_disk_devices().values()):
                disk_path = disk['source']
                stat_re = os.lstat(disk_path)
                backup_labels_of_disks[disk_path] = "%s:%s" % (stat_re.st_uid,
                                                               stat_re.st_gid)
                test.log.debug("TEST_STEP: Update image's owner to %s:%s",
                               uid, gid)
                os.chown(disk_path, uid, gid)

        test.log.info("TEST_STEP: Update VM XML with %s.", seclabel_attr)
        vmxml.set_seclabel([seclabel_attr])
        vmxml.sync()
        test.log.debug(VMXML.new_from_inactive_dumpxml(vm_name))

        test.log.info("TEST_STEP: Start the VM.")
        res = virsh.start(vm.name)
        libvirt.check_exit_status(res, status_error)
        if status_error:
            return

        test.log.info("TEST_STEP: Check the xattr of the vm image.")
        vm_first_disk = libvirt_disk.get_first_disk_source(vm)
        img_xattr = libvirt_disk.get_image_xattr(vm_first_disk)
        if not re.findall(xattr_selinux_str, img_xattr):
            test.fail("Unable to get %s!" % xattr_selinux_str)
        if re.findall(xattr_dac_str, img_xattr) == seclabel_relabel:
            test.fail("It should%s contain %s!"
                      % (' not' if seclabel_relabel else '', xattr_dac_str))

        test.log.info("TEST_STEP: Destroy the VM and check the xattr of image.")
        vm.destroy(gracefully=False)
        img_xattr = libvirt_disk.get_image_xattr(vm_first_disk)
        if img_xattr:
            test.fail("The xattr output should be cleaned up after VM shutdown!")

    finally:
        test.log.info("TEST_TEARDOWN: Recover test environment.")
        if qemu_conf_obj:
            libvirt.customize_libvirt_config(
                None, "qemu", config_object=qemu_conf_obj,
                is_recover=True)
        vm.destroy(gracefully=False)
        backup_xml.sync()
        for path, label in list(backup_labels_of_disks.items()):
            label_list = label.split(":")
            os.chown(path, int(label_list[0]), int(label_list[1]))

        if added_user:
            process.run("userdel -r %s" % added_user,
                        ignore_status=False, shell=True)
        restore_tpm_perms(vmxml, params)
