import os
import re
import shutil
import logging

from virttest.utils_test import libvirt
from avocado.utils import process

from virttest import data_dir
from virttest import libvirt_vm
from virttest import remote
from virttest import virsh
from virttest import utils_libvirtd
from virttest import ssh_key
from virttest import libvirt_version

LOG = logging.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test command: virsh destroy.

    The command can destroy (stop) a domain.
    1.Prepare test environment.
    2.When the ibvirtd == "off", stop the libvirtd service.
    3.Perform virsh destroy operation.
    4.Recover test environment.
    5.Confirm the test result.
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    domid = vm.get_id()
    domuuid = vm.get_uuid()

    vm_ref = params.get("destroy_vm_ref")
    status_error = params.get("status_error", "no")
    libvirtd = params.get("libvirtd", "on")
    remote_ip = params.get("remote_ip", "REMOTE.EXAMPLE.COM")
    remote_pwd = params.get("remote_pwd", None)
    local_ip = params.get("local_ip", "LOCAL.EXAMPLE.COM")
    local_pwd = params.get("local_pwd", "LOCAL.EXAMPLE.COM")
    paused_after_start_vm = "yes" == params.get("paused_after_start_vm", "no")
    destroy_readonly = "yes" == params.get("destroy_readonly", "no")
    start_destroy_times = params.get("start_destroy_times", "")
    limit_nofile = params.get("limit_nofile", "")
    if vm_ref == "remote" and (remote_ip.count("EXAMPLE.COM") or
                               local_ip.count("EXAMPLE.COM")):
        test.cancel(
            "Remote test parameters unchanged from default")

    uri = params.get("virsh_uri")
    unprivileged_user = params.get('unprivileged_user')
    if unprivileged_user:
        if unprivileged_user.count('EXAMPLE'):
            unprivileged_user = 'testacl'

    if not libvirt_version.version_compare(1, 1, 1):
        if params.get('setup_libvirt_polkit') == 'yes':
            test.cancel("API acl test not supported in current"
                        " libvirt version.")

    def modify_virtual_daemon(service_path, modify_info):
        """
        Modify libvirtd or virtqemud service

        :param service_path: service path
        :param modify_info: service modify info
        """
        ori_value = process.getoutput("cat %s | grep LimitNOFILE" % service_path, shell=True)
        with open(service_path, 'r+') as f:
            content = f.read()
            content = re.sub(ori_value, modify_info, content)
            f.seek(0)
            f.write(content)
            f.truncate()

    if vm_ref == "id":
        vm_ref = domid
    elif vm_ref == "hex_id":
        vm_ref = hex(int(domid))
    elif vm_ref.find("invalid") != -1:
        vm_ref = params.get(vm_ref)
    elif vm_ref == "name":
        vm_ref = "%s %s" % (vm_name, params.get("destroy_extra"))
    elif vm_ref == "uuid":
        vm_ref = domuuid

    if libvirtd == "off":
        utils_libvirtd.libvirtd_stop()

    if vm_ref != "remote":
        if paused_after_start_vm:
            virsh.suspend(vm_ref)
            if not vm.is_paused():
                test.fail("VM suspend failed")
        status = virsh.destroy(vm_ref, ignore_status=True,
                               unprivileged_user=unprivileged_user,
                               uri=uri, debug=True).exit_status
        output = ""

        if start_destroy_times:
            status = 0
            try:
                modify_service = "{}.service".format(
                                    utils_libvirtd.Libvirtd().service_name)
                LOG.debug("Modify service {}".format(modify_service))
                service_path = "/usr/lib/systemd/system/{}"\
                               .format(modify_service)
                LOG.debug("Service path is: {}".format(service_path))

                # Backup original libvirtd.service
                backup_file = os.path.join(data_dir.get_tmp_dir(),
                                           "{}-bak".format(modify_service))
                shutil.copy(service_path, backup_file)

                # Decrease domain number to speed up test
                modify_virtual_daemon(service_path, limit_nofile)
                process.run("systemctl daemon-reload")
                utils_libvirtd.Libvirtd(modify_service).restart()

                # Keep start/destroy guest to see whether domain unix socket
                # was cleaned up.
                for i in range(int(start_destroy_times)):
                    LOG.debug("Start guest {} times".format(i))
                    ret = virsh.start(vm_name)
                    if ret.exit_status:
                        test.fail("Failed to start guest: {}".format(ret.stderr_text))
                    virsh.destroy(vm_name)
            finally:
                # Recover libvirtd.service
                shutil.copy(backup_file, service_path)
                process.run("systemctl daemon-reload")
                utils_libvirtd.Libvirtd(modify_service).restart()

    else:
        status = 0
        try:
            remote_uri = libvirt_vm.complete_uri(local_ip)
            session = remote.remote_login("ssh", remote_ip, "22",
                                          "root", remote_pwd, "#")
            session.cmd_output('LANG=C')

            # Setup up remote to remote login in local host
            ssh_key.setup_remote_ssh_key(remote_ip, "root", remote_pwd,
                                         local_ip, "root", local_pwd)

            command = "virsh -c %s destroy %s" % (remote_uri, vm_name)
            status, output = session.cmd_status_output(command,
                                                       internal_timeout=5)
            session.close()
        except process.CmdError:
            status = 1

    if libvirtd == "off":
        utils_libvirtd.libvirtd_start()

    # Test the read_only mode
    if destroy_readonly:
        result = virsh.destroy(vm_ref, ignore_status=True, debug=True, readonly=True)
        libvirt.check_exit_status(result, expect_error=True)
        # This is for status_error check
        status = result.exit_status
        output = result

    # check status_error
    if status_error == "yes":
        if status == 0:
            if libvirtd == "off" and \
                    libvirt_version.version_compare(5, 6, 0):
                logging.info("From libvirt version 5.6.0 libvirtd is "
                             "restarted and destroy command should succeed")
            else:
                test.fail("Run successfully with destroy command! "
                          "Output:\n%s" % output)
    elif status_error == "no":
        if status != 0:
            test.fail("Run failed with destroy command! Output:\n%s"
                      % output)
