import os
import stat
import pwd
import grp
import logging as log

from avocado.utils import process
from avocado.utils import astring

from virttest import utils_selinux
from virttest import virt_vm
from virttest import gluster
from virttest import utils_config
from virttest import utils_libvirtd
from virttest import data_dir
from virttest import libvirt_version
from virttest.utils_test import libvirt as utlv
from virttest.libvirt_xml.vm_xml import VMXML


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


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


def format_user_group_id(vm_sec_label):
    """
    Transfer "user:group" str to integer user_id, integer group_id

    :param vm_sec_label: seclabel for the vm
    """
    label = vm_sec_label.split(":")
    user_info = format_user_group_str(label[0], label[1])
    user_id = int(user_info.split(":")[0])
    group_id = int(user_info.split(":")[1])
    return user_id, group_id


def run(test, params, env):
    """
    Test DAC setting in both domain xml and qemu.conf.

    (1) Init variables for test.
    (2) Set VM xml and qemu.conf with proper DAC label, also set
        monitor socket parent dir with propoer ownership and mode.
    (3) Start VM and check the context.
    """

    # Get general variables.
    status_error = ('yes' == params.get("status_error", 'no'))
    host_sestatus = params.get("host_selinux", "enforcing")
    swtpm_lib = params.get('swtpm_lib')
    # Get variables about seclabel for VM.
    sec_type = params.get("vm_sec_type", "dynamic")
    vm_sec_model = params.get("vm_sec_model", "dac")
    vm_sec_label = params.get("vm_sec_label", None)
    vm_sec_relabel = params.get("vm_sec_relabel", "yes")
    sec_dict = {'type': sec_type, 'model': vm_sec_model,
                'relabel': vm_sec_relabel}
    if vm_sec_label:
        sec_dict['label'] = vm_sec_label
    set_qemu_conf = "yes" == params.get("set_qemu_conf", "no")
    # Get per-img seclabel variables
    disk_type = params.get("disk_type")
    disk_target = params.get('disk_target')
    disk_src_protocol = params.get("disk_source_protocol")
    vol_name = params.get("vol_name")
    tmp_dir = data_dir.get_tmp_dir()
    pool_name = params.get("pool_name", "gluster-pool")
    brick_path = os.path.join(tmp_dir, pool_name)
    invalid_label = 'yes' == params.get("invalid_label", "no")
    relabel = params.get("per_img_sec_relabel")
    sec_label = params.get("per_img_sec_label")
    per_sec_model = params.get("per_sec_model", 'dac')
    per_img_dict = {'sec_model': per_sec_model, 'relabel': relabel,
                    'sec_label': sec_label}
    params.update(per_img_dict)
    # Get qemu.conf config variables
    qemu_user = params.get("qemu_user", 'qemu')
    qemu_group = params.get("qemu_group", 'qemu')
    dynamic_ownership = "yes" == params.get("dynamic_ownership", "yes")

    # When using nfs, the virt_use_nfs should be enabled
    enable_virt_use_nfs = 'yes' == params.get("virt_use_nfs", 'no')
    # Get variables about VM and get a VM object and VMXML instance.
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vmxml = VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()

    # Set selinux of host.
    backup_sestatus = utils_selinux.get_status()
    if backup_sestatus == "disabled":
        test.cancel("SELinux is in Disabled "
                    "mode. it must be in Enforcing "
                    "mode to run this test")
    utils_selinux.set_status(host_sestatus)
    if enable_virt_use_nfs:
        process.run("setsebool virt_use_nfs on", shell=True)

    qemu_sock_mod = False
    qemu_sock_path = '/var/lib/libvirt/qemu/'
    qemu_conf = utils_config.LibvirtQemuConfig()
    libvirtd = utils_libvirtd.Libvirtd()
    try:
        if set_qemu_conf or sec_type == 'static':
            # Set qemu.conf for user and group
            if sec_type == 'static':
                if vmxml.devices.by_device_tag('tpm') is not None:
                    user_id, group_id = format_user_group_id(vm_sec_label)
                    if user_id != 107 or group_id != 107:
                        qemu_conf.swtpm_user = user_id
                        qemu_conf.swtpm_group = group_id
                        set_tpm_perms(swtpm_lib)
                qemu_conf.dynamic_ownership = 1
                dynamic_ownership = True
            if qemu_user:
                qemu_conf.user = qemu_user
            if qemu_group:
                qemu_conf.group = qemu_group
            if dynamic_ownership:
                qemu_conf.dynamic_ownership = 1
            else:
                qemu_conf.dynamic_ownership = 0
            if not dynamic_ownership or not sec_label:
                if vmxml.devices.by_device_tag('tpm') is not None:
                    set_tpm_perms(swtpm_lib)
            logging.debug("the qemu.conf content is: %s" % qemu_conf)
            libvirtd.restart()
            st = os.stat(qemu_sock_path)
            if not bool(st.st_mode & stat.S_IWGRP):
                # chmod g+w
                os.chmod(qemu_sock_path, st.st_mode | stat.S_IWGRP)
                qemu_sock_mod = True

        # Set the context of the VM.
        logging.debug("sec_dict is %s" % sec_dict)
        vmxml.set_seclabel([sec_dict])
        vmxml.sync()

        # Get per-image seclabel in id string
        if sec_label:
            per_img_usr, per_img_grp = sec_label.split(':')
            sec_label_id = format_user_group_str(per_img_usr, per_img_grp)

        # Start VM to check the qemu process and image.
        try:
            # Set per-img sec context and start vm
            utlv.set_vm_disk(vm, params)
            # Start VM successfully.
            if status_error:
                if invalid_label:
                    # invalid label should fail, more info in bug 1165485
                    logging.debug("The guest failed to start as expected,"
                                  "details see bug: bugzilla.redhat.com/show_bug.cgi"
                                  "?id=1165485")
                else:
                    test.fail("Test succeeded in negative case.")

            # Get vm process label when VM is running.
            vm_pid = vm.get_pid()
            pid_stat = os.stat("/proc/%d" % vm_pid)
            vm_process_uid = pid_stat.st_uid
            vm_process_gid = pid_stat.st_gid
            vm_context = "%s:%s" % (vm_process_uid, vm_process_gid)
            logging.debug("vm process label is: %s", vm_context)

            # Get vm image label when VM is running
            if disk_type != "network":
                disks = vm.get_blk_devices()
                if libvirt_version.version_compare(3, 1, 0) and disk_type == "block":
                    output = astring.to_text(process.system_output(
                        "nsenter -t %d -m -- ls -l %s" % (vm_pid, disks[disk_target]['source'])))
                    owner, group = output.strip().split()[2:4]
                    disk_context = format_user_group_str(owner, group)
                else:
                    stat_re = os.stat(disks[disk_target]['source'])
                    disk_context = "%s:%s" % (stat_re.st_uid, stat_re.st_gid)
                logging.debug("The disk dac label after vm start is: %s",
                              disk_context)
                if sec_label and relabel == 'yes':
                    if disk_context != sec_label_id:
                        test.fail("The disk label is not equal to "
                                  "'%s'." % sec_label_id)

        except virt_vm.VMStartError as e:
            # Starting VM failed.
            if not status_error:
                test.fail("Test failed in positive case."
                          "error: %s" % e)
    finally:
        # clean up
        if vm.is_alive():
            vm.destroy(gracefully=False)
        backup_xml.sync()
        if qemu_sock_mod:
            st = os.stat(qemu_sock_path)
            os.chmod(qemu_sock_path, st.st_mode ^ stat.S_IWGRP)
        if set_qemu_conf:
            qemu_conf.restore()
            libvirtd.restart()
        utils_selinux.set_status(backup_sestatus)
        if disk_src_protocol == 'iscsi':
            utlv.setup_or_cleanup_iscsi(is_setup=False)
        elif disk_src_protocol == 'gluster':
            gluster.setup_or_cleanup_gluster(False, brick_path=brick_path, **params)
            libvirtd.restart()
        elif disk_src_protocol == 'netfs':
            utlv.setup_or_cleanup_nfs(is_setup=False,
                                      restore_selinux=backup_sestatus)
        if vmxml.devices.by_device_tag('tpm') is not None:
            if os.path.isfile('/tmp/permis.facl'):
                cmd = "setfacl --restore=/tmp/permis.facl"
                process.run(cmd, ignore_status=True, shell=True)
                os.unlink('/tmp/permis.facl')
