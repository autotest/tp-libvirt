import re
import os

from virttest import virsh
from virttest import data_dir
from virttest import utils_libvirtd

from provider import libvirt_version


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

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    session = vm.wait_for_login()

    os_type = params.get("os_type")
    status_error = ("yes" == params.get("status_error"))
    libvirtd = params.get("libvirtd", "on")
    extra_param = params.get("restore_extra_param")
    pre_status = params.get("restore_pre_status")
    vm_ref = params.get("restore_vm_ref")
    uri = params.get("virsh_uri")
    unprivileged_user = params.get('unprivileged_user')
    if unprivileged_user:
        if unprivileged_user.count('EXAMPLE'):
            unprivileged_user = 'testacl'

    if not libvirt_version.version_compare(1, 1, 1):
        if params.get('setup_libvirt_polkit') == 'yes':
            test.cancel("API acl test not supported in current"
                        " libvirt version.")

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
        virsh.save(vm_name, tmp_file)
        if vm_ref == "saved_file":
            vm_ref = tmp_file
        elif vm_ref == "empty_new_file":
            tmp_file = os.path.join(data_dir.get_tmp_dir(), "new.file")
            with open(tmp_file, 'w') as tmp:
                pass
            vm_ref = tmp_file
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

    try:
        if status_error:
            if not status:
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
            if extra_param.count("running"):
                if vm.is_dead() or vm.is_paused():
                    test.fail("Guest state should be"
                              " running after restore"
                              " due to the option --running")
    finally:
        if vm.is_paused():
            virsh.resume(vm_name)
