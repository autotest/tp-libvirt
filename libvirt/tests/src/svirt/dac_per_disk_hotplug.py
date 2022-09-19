import os
import pwd
import grp
import logging as log

from avocado.utils import process

from virttest import qemu_storage
from virttest import data_dir
from virttest import virsh
from virttest import utils_config
from virttest import utils_libvirtd
from virttest.utils_test import libvirt as utlv
from virttest.libvirt_xml.vm_xml import VMXML

from virttest import libvirt_version


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def check_ownership(file_path):
    """
    Return file ownership string user:group

    :params file_path: the file path
    :return: ownership string user:group or false when file not exist
    """
    try:
        f = os.open(file_path, 0)
    except OSError:
        return False
    stat_re = os.fstat(f)
    label = "%s:%s" % (stat_re.st_uid, stat_re.st_gid)
    os.close(f)
    logging.debug("File %s ownership is: %s" % (file_path, label))
    return label


def format_user_group_str(user, group):
    """
    Check given user and group, then return "uid:gid" string

    :param user: given user name or id string
    :param group: given group name or id string
    :return: "uid:gid" string
    """
    try:
        user_id = int(user)
    except ValueError:
        try:
            user_id = pwd.getpwnam(user).pw_uid
        except KeyError:
            # user did not exist will definitely fail start domain, log warning
            # here, let the test continue
            logging.warning("the user name: %s not found on host" % user)
            user_id = user

    try:
        grp_id = int(group)
    except ValueError:
        try:
            grp_id = grp.getgrnam(group).gr_gid
        except KeyError:
            # group name not exist will fail start vm, only add warning info
            # here, let the test continue
            logging.warning("the group name: %s not found on host" % group)
            grp_id = group

    label_str = "%s:%s" % (user_id, grp_id)
    return label_str


def set_tpm_perms(swtpm_lib):
    """
    Set swtpm_user/swtpm_group in qemu conf and swtpm_lib permission
    if dynamic_ownership is disabled

    :param swtpm_lib: the dir of swtpm lib
    """
    cmd = "getfacl -pR %s > /tmp/permis.facl" % swtpm_lib
    process.run(cmd, ignore_status=True, shell=True)
    cmd = "chmod -R 777 %s" % swtpm_lib
    process.run(cmd, ignore_status=False, shell=True)


def set_nvram_perms(vars_path, qemu_user, qemu_group):
    """
    Set nvram file permission when dynamic_ownership is disabled

    :param vars_path: the path of nvram file
    :param qemu_user: qemu_user set in qemu.conf
    :param qemu_group: group_user set in qemu.conf
    """
    if vars_path is not None and os.path.exists(vars_path):
        user_info = format_user_group_str(qemu_user, qemu_group)
        user_id = int(user_info.split(":")[0])
        group_id = int(user_info.split(":")[1])
        os.chown(vars_path, user_id, group_id)


def run(test, params, env):
    """
    Test per-image DAC disk hotplug to VM.

    (1).Init variables for test.
    (2).Create disk xml with per-image DAC
    (3).Start VM
    (4).Attach the disk to VM and check result.
    """
    # Get general variables.
    status_error = ('yes' == params.get("status_error", 'no'))
    # Get qemu.conf config variables
    qemu_user = params.get("qemu_user")
    qemu_group = params.get("qemu_group")
    dynamic_ownership = "yes" == params.get("dynamic_ownership", "yes")

    # Get per-image DAC setting
    vol_name = params.get('vol_name')
    target_dev = params.get('target_dev')
    disk_type_name = params.get("disk_type_name")
    img_user = params.get("img_user")
    img_group = params.get("img_group")
    relabel = 'yes' == params.get('relabel', 'yes')
    vars_path = params.get('vars_path')
    swtpm_lib = params.get('swtpm_lib')

    if not libvirt_version.version_compare(1, 2, 7):
        test.cancel("per-image DAC only supported on version 1.2.7"
                    " and after.")

    # Get variables about VM and get a VM object and VMXML instance.
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vmxml = VMXML.new_from_inactive_dumpxml(vm_name)

    img_path = None
    qemu_conf = utils_config.LibvirtQemuConfig()
    libvirtd = utils_libvirtd.Libvirtd()
    try:
        # set qemu conf
        qemu_conf.user = qemu_user
        qemu_conf.group = qemu_group
        if dynamic_ownership:
            qemu_conf.dynamic_ownership = 1
        else:
            qemu_conf.dynamic_ownership = 0
            if vmxml.devices.by_device_tag('tpm') is not None:
                qemu_conf.swtpm_user = qemu_user
                qemu_conf.swtpm_group = qemu_group
                set_tpm_perms(swtpm_lib)
            if vmxml.os.xmltreefile.find('nvram') is not None:
                vars_path = vmxml.os.nvram
            elif vmxml.os.fetch_attrs().get('os_firmware') == 'efi':
                vars_path = params.get('vars_path')
            set_nvram_perms(vars_path, qemu_user, qemu_group)

        logging.debug("the qemu.conf content is: %s" % qemu_conf)
        libvirtd.restart()

        first_disk = vm.get_first_disk_devices()
        blk_source = first_disk['source']
        owner_str = format_user_group_str(qemu_user, qemu_group)
        src_usr, src_grp = owner_str.split(':')
        os.chown(blk_source, int(src_usr), int(src_grp))
        xml = VMXML.new_from_inactive_dumpxml(vm_name)
        vm.start()

        # Init a QemuImg instance and create a img.
        params['image_name'] = vol_name
        tmp_dir = data_dir.get_tmp_dir()
        image = qemu_storage.QemuImg(params, tmp_dir, vol_name)
        # Create a image.
        img_path, result = image.create(params)

        # Create disk xml for attach.
        params['source_file'] = img_path
        sec_label = "%s:%s" % (img_user, img_group)
        params['sec_label'] = sec_label
        params['type_name'] = disk_type_name
        sec_label_id = format_user_group_str(img_user, img_group)

        disk_xml = utlv.create_disk_xml(params)

        # Change img file to qemu:qemu and 660 mode
        os.chown(img_path, 107, 107)
        os.chmod(img_path, 432)

        img_label_before = check_ownership(img_path)
        if img_label_before:
            logging.debug("the image ownership before "
                          "attach: %s" % img_label_before)

        # Do the attach action.
        option = "--persistent"
        result = virsh.attach_device(vm_name, filearg=disk_xml,
                                     flagstr=option, debug=True)
        utlv.check_exit_status(result, status_error)

        if not result.exit_status:
            img_label_after = check_ownership(img_path)
            if dynamic_ownership and relabel:
                if img_label_after != sec_label_id:
                    test.fail("The image dac label %s is not "
                              "expected." % img_label_after)

            ret = virsh.detach_disk(vm_name, target=target_dev,
                                    extra=option,
                                    debug=True)
            utlv.check_exit_status(ret, status_error)
    finally:
        # clean up
        vm.destroy()
        qemu_conf.restore()
        vmxml.sync()
        libvirtd.restart()
        if vmxml.devices.by_device_tag('tpm') is not None:
            if os.path.isfile('/tmp/permis.facl'):
                cmd = "setfacl --restore=/tmp/permis.facl"
                process.run(cmd, ignore_status=True, shell=True)
                os.unlink('/tmp/permis.facl')
        if img_path and os.path.exists(img_path):
            os.unlink(img_path)
