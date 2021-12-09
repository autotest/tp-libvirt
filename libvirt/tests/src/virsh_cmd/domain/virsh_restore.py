import re
import os
import stat
import logging
import time
import pwd
import grp

from avocado.utils import process

from virttest import virsh
from virttest import data_dir
from virttest import utils_libvirtd
from virttest import libvirt_version

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Test command: virsh restore.

    Restore a domain from a saved state in a file
    1.Prepare test environment.
    2.When the libvirtd == "off", stop the libvirtd service.
    3.Run virsh restore command with assigned option.
    4.Recover test environment.
    5.Confirm the test result.
    """
    def check_file_own(file_path, exp_uid, exp_gid):
        """
        Check the uid and gid of file_path

        :param file_path: The file path
        :param exp_uid: The expected uid
        :param exp_gid: The expected gid
        :raise: test.fail if the uid and gid of file are not expected
        """
        fstat_res = os.stat(file_path)
        if fstat_res.st_uid != exp_uid or fstat_res.st_gid != exp_gid:
            test.fail("The uid.gid {}.{} is not expected, it should be {}.{}."
                      .format(fstat_res.st_uid, fstat_res.st_gid,
                              exp_uid, exp_gid))

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    os_type = params.get("os_type")
    status_error = ("yes" == params.get("status_error"))
    libvirtd = params.get("libvirtd", "on")
    extra_param = params.get("restore_extra_param")
    pre_status = params.get("restore_pre_status")
    vm_ref = params.get("restore_vm_ref")
    uri = params.get("virsh_uri")
    unprivileged_user = params.get('unprivileged_user')
    time_before_save = int(params.get('time_before_save', 0))
    setup_nfs = "yes" == params.get("setup_nfs", "no")
    setup_iscsi = "yes" == params.get("setup_iscsi", "no")
    check_log = params.get("check_log")
    check_str_not_in_log = params.get("check_str_not_in_log")
    qemu_conf_dict = eval(params.get("qemu_conf_dict", "{}"))

    vm_ref_uid = None
    vm_ref_gid = None
    qemu_conf = None

    if unprivileged_user:
        if unprivileged_user.count('EXAMPLE'):
            unprivileged_user = 'testacl'

    if not libvirt_version.version_compare(1, 1, 1):
        if params.get('setup_libvirt_polkit') == 'yes':
            test.cancel("API acl test not supported in current"
                        " libvirt version.")
    try:
        if "--xml" in extra_param:
            vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name, options="--migratable")
            backup_xml = vmxml.copy()
            # Grant more priveledge on the file in order for un-priveledge user
            # to access.
            os.chmod(vmxml.xml, stat.S_IRWXU | stat.S_IRGRP | stat.S_IROTH)
            if not setup_nfs:
                extra_param = "--xml %s" % vmxml.xml
                dict_os_attrs = {}
                if "hd" in vmxml.os.boots:
                    dict_os_attrs.update({"boots": ["cdrom"]})
                    vmxml.set_os_attrs(**dict_os_attrs)
                else:
                    test.cancel("Please add 'hd' in boots for --xml testing")
                logging.info("vmxml os is %s after update"
                             % vmxml.os.xmltreefile)
            else:
                params["mnt_path_name"] = params.get("nfs_mount_dir")
                vm_ref_uid = params["change_file_uid"] = pwd.getpwnam("qemu").pw_uid
                vm_ref_gid = params["change_file_gid"] = grp.getgrnam("qemu").gr_gid
                libvirt.set_vm_disk(vm, params)

        session = vm.wait_for_login()
        # Clear log file
        if check_log:
            cmd = "> %s" % check_log
            process.run(cmd, shell=True, verbose=True)
        if qemu_conf_dict:
            logging.debug("Update qemu configuration file.")
            qemu_conf = libvirt.customize_libvirt_config(qemu_conf_dict, "qemu")
            process.run("cat /etc/libvirt/qemu.conf", shell=True, verbose=True)

        # run test
        if vm_ref == "" or vm_ref == "xyz":
            status = virsh.restore(vm_ref, extra_param, debug=True,
                                   unprivileged_user=unprivileged_user,
                                   uri=uri,
                                   ignore_status=True).exit_status
        else:
            if os_type == "linux":
                cmd = "cat /proc/cpuinfo"
                try:
                    status, output = session.cmd_status_output(cmd, timeout=10)
                finally:
                    session.close()
                if not re.search("processor", output):
                    test.fail("Unable to read /proc/cpuinfo")
            tmp_file = os.path.join(data_dir.get_tmp_dir(), "save.file")
            if setup_iscsi:
                tmp_file = libvirt.setup_or_cleanup_iscsi(
                    is_setup=True, is_login=True, image_size='1G')
            time.sleep(time_before_save)
            ret = virsh.save(vm_name, tmp_file, debug=True)
            libvirt.check_exit_status(ret)
            if vm_ref == "saved_file" or setup_iscsi:
                vm_ref = tmp_file
            elif vm_ref == "empty_new_file":
                tmp_file = os.path.join(data_dir.get_tmp_dir(), "new.file")
                with open(tmp_file, 'w') as tmp:
                    pass
                vm_ref = tmp_file

            # Change the ownership of the saved file
            if vm_ref_uid and vm_ref_gid:
                os.chown(vm_ref, vm_ref_uid, vm_ref_gid)
                tmpdir = data_dir.get_tmp_dir()
                dump_xml = os.path.join(tmpdir, "test.xml")
                virsh.save_image_dumpxml(vm_ref, "> %s" % dump_xml)
                extra_param = "--xml %s" % dump_xml
                check_file_own(vm_ref, vm_ref_uid, vm_ref_gid)

            if vm.is_alive():
                vm.destroy()
            if pre_status == "start":
                virsh.start(vm_name)
            if libvirtd == "off":
                utils_libvirtd.libvirtd_stop()
            status = virsh.restore(vm_ref, extra_param, debug=True,
                                   unprivileged_user=unprivileged_user,
                                   uri=uri,
                                   ignore_status=True).exit_status
        if not status_error:
            list_output = virsh.dom_list().stdout.strip()

        session.close()

        # recover libvirtd service start
        if libvirtd == "off":
            utils_libvirtd.libvirtd_start()

        if status_error:
            if not status:
                if libvirtd == "off" and libvirt_version.version_compare(5, 6, 0):
                    logging.info("From libvirt version 5.6.0 libvirtd is restarted "
                                 "and command should succeed")
                else:
                    test.fail("Run successfully with wrong command!")
        else:
            if status:
                test.fail("Run failed with right command")
            if not re.search(vm_name, list_output):
                test.fail("Run failed with right command")
            if extra_param.count("paused"):
                if not vm.is_paused():
                    test.fail("Guest state should be"
                              " paused after restore"
                              " due to the option --paused")
            if (extra_param.count("running") or
                    extra_param.count("xml") or
                    not extra_param):
                if vm.is_dead() or vm.is_paused():
                    test.fail("Guest state should be"
                              " running after restore")
            if extra_param.count("xml"):
                if not setup_nfs:
                    aft_vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
                    boots_list = aft_vmxml.os.boots
                    if "hd" in boots_list or "cdrom" not in boots_list:
                        test.fail("Update xml with restore failed")
                else:
                    if vm_ref_uid and vm_ref_gid:
                        check_file_own(vm_ref, vm_ref_uid, vm_ref_gid)
                        vm.destroy()
                        check_file_own(vm_ref, vm_ref_uid, vm_ref_gid)
            if check_str_not_in_log and check_log:
                libvirt.check_logfile(check_str_not_in_log, check_log, False)
    finally:
        if vm.is_paused():
            virsh.resume(vm_name)
        if "--xml" in extra_param:
            backup_xml.sync()
        if setup_nfs:
            libvirt.setup_or_cleanup_nfs(
                is_setup=False, mount_dir=params.get("mnt_path_name"),
                export_dir=params.get("export_dir"), rm_export_dir=False)
        if setup_iscsi:
            libvirt.setup_or_cleanup_iscsi(False)
