import os
import stat
import pwd
import grp
import logging

from avocado.utils import process

from virttest import utils_selinux
from virttest import virt_vm
from virttest import utils_config
from virttest import utils_libvirtd
from virttest import libvirt_version
from virttest.libvirt_xml.vm_xml import VMXML


def check_qemu_grp_user(user, test):
    """
    Check the given user exist and in 'qemu' group

    :param user: given user name or id
    :param test: Test object
    :return: True or False
    """
    try:
        # check the user exist or not
        user_id = None
        user_name = None
        try:
            user_id = int(user)
        except ValueError:
            user_name = user
        if user_id:
            pw_user = pwd.getpwuid(user_id)
        else:
            pw_user = pwd.getpwnam(user_name)
        user_name = pw_user.pw_name

        # check the user is in 'qemu' group
        grp_names = []
        for g in grp.getgrall():
            if user_name in g.gr_mem:
                grp_names.append(g.gr_name)
                grp_names.append(str(g.gr_gid))
        if "qemu" in grp_names:
            return True
        else:
            err_msg = "The given user: %s exist " % user
            err_msg += "but not in 'qemu' group."
            test.fail(err_msg)
    except KeyError:
        return False


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
            # user did not exist will definitly fail start domain, log warning
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


def run(test, params, env):
    """
    Test DAC setting in both domain xml and qemu.conf.

    (1) Init variables for test.
    (2) Set VM xml and qemu.conf with proper DAC label, also set image and
        monitor socket parent dir with propoer ownership and mode.
    (3) Start VM and check the context.
    (4) Destroy VM and check the context.
    """

    # Get general variables.
    status_error = ('yes' == params.get("status_error", 'no'))
    host_sestatus = params.get("dac_start_destroy_host_selinux", "enforcing")
    qemu_group_user = "yes" == params.get("qemu_group_user", "no")
    # Get variables about seclabel for VM.
    sec_type = params.get("dac_start_destroy_vm_sec_type", "dynamic")
    sec_model = params.get("dac_start_destroy_vm_sec_model", "dac")
    sec_label = params.get("dac_start_destroy_vm_sec_label", None)
    sec_relabel = params.get("dac_start_destroy_vm_sec_relabel", "yes")
    security_default_confined = params.get("security_default_confined", None)
    set_process_name = params.get("set_process_name", None)
    sec_dict = {'type': sec_type, 'model': sec_model, 'relabel': sec_relabel}
    if sec_label:
        sec_dict['label'] = sec_label
    set_sec_label = "yes" == params.get("set_sec_label", "no")
    set_qemu_conf = "yes" == params.get("set_qemu_conf", "no")
    qemu_no_usr_grp = "yes" == params.get("qemu_no_usr_grp", "no")
    # Get qemu.conf config variables
    qemu_user = params.get("qemu_user", None)
    qemu_group = params.get("qemu_group", None)
    dynamic_ownership = "yes" == params.get("dynamic_ownership", "yes")

    # Get variables about VM and get a VM object and VMXML instance.
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vmxml = VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()

    # Get varialbles about image.
    img_label = params.get('dac_start_destroy_disk_label')
    # Label the disks of VM with img_label.
    disks = vm.get_disk_devices()
    backup_labels_of_disks = {}
    qemu_disk_mod = False
    if (not status_error):
        for disk in list(disks.values()):
            disk_path = disk['source']
            f = os.open(disk_path, 0)
            stat_re = os.fstat(f)
            backup_labels_of_disks[disk_path] = "%s:%s" % (stat_re.st_uid,
                                                           stat_re.st_gid)
            label_list = img_label.split(":")
            os.chown(disk_path, int(label_list[0]), int(label_list[1]))
            os.close(f)
            st = os.stat(disk_path)
            if not bool(st.st_mode & stat.S_IWGRP):
                # add group wirte mode to disk by chmod g+w
                os.chmod(disk_path, st.st_mode | stat.S_IWGRP)
                qemu_disk_mod = True

    # Set selinux of host.
    backup_sestatus = utils_selinux.get_status()
    if backup_sestatus == "disabled":
        test.cancel("SELinux is in Disabled "
                    "mode. it must be in Enforcing "
                    "mode to run this test")
    utils_selinux.set_status(host_sestatus)

    def _create_user():
        """
        Create a "vdsm_fake" in 'qemu' group for test
        """
        logging.debug("create a user 'vdsm_fake' in 'qemu' group")
        cmd = "useradd vdsm_fake -G qemu -s /sbin/nologin"
        process.run(cmd, ignore_status=False, shell=True)

    create_qemu_user = False
    qemu_sock_mod = False
    qemu_sock_path = '/var/lib/libvirt/qemu/'
    qemu_conf = utils_config.LibvirtQemuConfig()
    libvirtd = utils_libvirtd.Libvirtd()
    try:
        # Check qemu_group_user
        if qemu_group_user:
            if set_qemu_conf:
                if "EXAMPLE" in qemu_user:
                    if not check_qemu_grp_user("vdsm_fake", test):
                        _create_user()
                        create_qemu_user = True
                    qemu_user = "vdsm_fake"
                    qemu_group = "qemu"
            if set_sec_label:
                if sec_label:
                    if "EXAMPLE" in sec_label:
                        if not check_qemu_grp_user("vdsm_fake", test):
                            _create_user()
                            create_qemu_user = True
                        sec_label = "vdsm_fake:qemu"
                        sec_dict['label'] = sec_label
            st = os.stat(qemu_sock_path)
            if not bool(st.st_mode & stat.S_IWGRP):
                # chmod g+w
                os.chmod(qemu_sock_path, st.st_mode | stat.S_IWGRP)
                qemu_sock_mod = True

        if set_qemu_conf:
            # Transform qemu user and group to "uid:gid"
            qemu_user = qemu_user.replace("+", "")
            qemu_group = qemu_group.replace("+", "")
            qemu_conf_label_trans = format_user_group_str(qemu_user, qemu_group)

            # Set qemu.conf for user and group
            if qemu_user:
                qemu_conf.user = qemu_user
            if qemu_group:
                qemu_conf.group = qemu_group
            if dynamic_ownership:
                qemu_conf.dynamic_ownership = 1
            else:
                qemu_conf.dynamic_ownership = 0
            if security_default_confined:
                qemu_conf.security_default_confined = security_default_confined
            if set_process_name:
                qemu_conf.set_process_name = set_process_name
            logging.debug("the qemu.conf content is: %s" % qemu_conf)

        if set_sec_label:
            # Transform seclabel to "uid:gid"
            if sec_label:
                sec_label = sec_label.replace("+", "")
                if ":" in sec_label:
                    user, group = sec_label.split(":")
                    sec_label_trans = format_user_group_str(user, group)

            # Set the context of the VM.
            logging.debug("sec_dict is %s" % sec_dict)
            vmxml.set_seclabel([sec_dict])
            vmxml.sync()
            logging.debug("updated domain xml is: %s" % vmxml.xmltreefile)

        # Start VM to check the qemu process and image.
        try:
            libvirtd.restart()
            vm.start()
            # Start VM successfully.
            # VM with seclabel can access the image with the context.
            if status_error:
                test.fail("Test succeeded in negative case.")

            # Get vm process label when VM is running.
            vm_pid = vm.get_pid()
            pid_stat = os.stat("/proc/%d" % vm_pid)
            vm_process_uid = pid_stat.st_uid
            vm_process_gid = pid_stat.st_gid
            vm_context = "%s:%s" % (vm_process_uid, vm_process_gid)

            # Get vm image label when VM is running
            f = os.open(list(disks.values())[0]['source'], 0)
            stat_re = os.fstat(f)
            disk_context = "%s:%s" % (stat_re.st_uid, stat_re.st_gid)
            os.close(f)

            # Check vm process and image DAC label after vm start
            if set_sec_label and sec_label:
                if ":" in sec_label:
                    if vm_context != sec_label_trans:
                        test.fail("Label of VM processs is not "
                                  "expected after starting.\nDetail:"
                                  "vm_context=%s, sec_label_trans=%s"
                                  % (vm_context, sec_label_trans))
                    if sec_relabel == "yes":
                        if dynamic_ownership:
                            if disk_context != sec_label_trans:
                                test.fail("Label of disk is not " +
                                          "expected" +
                                          " after VM starting.\n" +
                                          "Detail: disk_context" +
                                          "=%s" % disk_context +
                                          ", sec_label_trans=%s."
                                          % sec_label_trans)
            elif(set_qemu_conf and not security_default_confined and not
                 qemu_no_usr_grp):
                if vm_context != qemu_conf_label_trans:
                    test.fail("Label of VM processs is not expected"
                              " after starting.\nDetail: vm_context="
                              "%s, qemu_conf_label_trans=%s"
                              % (vm_context, qemu_conf_label_trans))
                if disk_context != qemu_conf_label_trans:
                    if dynamic_ownership:
                        test.fail("Label of disk is not expected " +
                                  "after VM starting.\nDetail: di" +
                                  "sk_context=%s, " % disk_context +
                                  "qemu_conf_label_trans=%s." %
                                  qemu_conf_label_trans)

            # check vm started with -name $vm_name,process=qemu:$vm_name
            if set_process_name:
                if libvirt_version.version_compare(1, 3, 5):
                    chk_str = "-name guest=%s,process=qemu:%s" % (vm_name, vm_name)
                else:
                    chk_str = "-name %s,process=qemu:%s" % (vm_name, vm_name)
                cmd = "ps -p %s -o command=" % vm_pid
                result = process.run(cmd, shell=True)
                if chk_str in result.stdout_text:
                    logging.debug("%s found in vm process command: %s" %
                                  (chk_str, result.stdout_text))
                else:
                    test.fail("%s not in vm process command: %s" %
                              (chk_str, result.stdout_text))

            # Check the label of disk after VM being destroyed.
            vm.destroy()
            f = os.open(list(disks.values())[0]['source'], 0)
            stat_re = os.fstat(f)
            img_label_after = "%s:%s" % (stat_re.st_uid, stat_re.st_gid)
            os.close(f)
            if libvirt_version.version_compare(5, 6, 0):
                if img_label_after != img_label:
                    test.fail("Label of disk is img_label_after"
                              ":%s" % img_label_after + ", it "
                              "did not restore to %s in VM "
                              "shuting down." % img_label)
            elif set_sec_label and sec_relabel == "yes":
                # As dynamic_ownership as 1 on non-share fs, current domain
                # image will restore to 0:0 when sec_relabel enabled.
                if dynamic_ownership:
                    if not img_label_after == "0:0":
                        test.fail("Label of disk is img_label_after"
                                  ":%s" % img_label_after + ", it "
                                  "did not restore to 0:0 in VM "
                                  "shuting down.")
            elif set_qemu_conf and not set_sec_label:
                # As dynamic_ownership as 1 on non-share fs, current domain
                # image will restore to 0:0 when only set qemu.conf.
                if dynamic_ownership:
                    if not img_label_after == "0:0":
                        test.fail("Label of disk is img_label_after"
                                  ":%s" % img_label_after + ", it "
                                  "did not restore to 0:0 in VM "
                                  "shuting down.")
                else:
                    if (not img_label_after == img_label):
                        test.fail("Bug: Label of disk is changed\n"
                                  "Detail: img_label_after=%s, "
                                  "img_label=%s.\n"
                                  % (img_label_after, img_label))
        except virt_vm.VMStartError as e:
            # Starting VM failed.
            # VM with seclabel can not access the image with the context.
            if not status_error:
                err_msg = "Domain start failed as expected, check "
                err_msg += "more in https://bugzilla.redhat.com/show_bug"
                err_msg += ".cgi?id=856951"
                if set_sec_label:
                    if sec_label:
                        if sec_relabel == "yes" and sec_label_trans == "0:0":
                            if set_qemu_conf and not qemu_no_usr_grp:
                                if qemu_conf_label_trans == "107:107":
                                    logging.debug(err_msg)
                        elif sec_relabel == "no" and sec_label_trans == "0:0":
                            if not set_qemu_conf:
                                logging.debug(err_msg)
                else:
                    test.fail("Test failed in positive case."
                              "error: %s" % e)
    finally:
        # clean up
        for path, label in list(backup_labels_of_disks.items()):
            label_list = label.split(":")
            os.chown(path, int(label_list[0]), int(label_list[1]))
            if qemu_disk_mod:
                st = os.stat(path)
                os.chmod(path, st.st_mode ^ stat.S_IWGRP)
        if set_sec_label:
            backup_xml.sync()
        if qemu_sock_mod:
            st = os.stat(qemu_sock_path)
            os.chmod(qemu_sock_path, st.st_mode ^ stat.S_IWGRP)
        if set_qemu_conf:
            qemu_conf.restore()
            libvirtd.restart()
        if create_qemu_user:
            cmd = "userdel -r vdsm_fake"
            output = process.run(cmd, ignore_status=True, shell=True)
        utils_selinux.set_status(backup_sestatus)
