import os
import stat

from avocado.utils import process

from virttest import virsh
from virttest import libvirt_version
from virttest import ssh_key

from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml


def run(test, params, env):
    """
    Test set-user-sshkeys command
    """
    def prepare_user_sshkeys(user):
        """
        Generate the ssh key for user, and return the path

        :param user: The name of the user to generate ssh key for
        :return: The path to the generated ssh public key file
        """
        process.run('useradd %s' % user, shell=True)
        ssh_key.get_public_key(client_user=user)
        ssh_key_path = "/home/%s/.ssh/id_rsa.pub" % user

        return ssh_key_path

    def add_other_read_permission(path):
        """
        Add permissions for others to read the content of the file.

        :param path: The path of the file
        """
        def _add_permission(path, permission):
            current_mode = os.stat(path).st_mode
            new_mode = current_mode | permission
            os.chmod(path, new_mode)

        dir_path = os.path.dirname(path)
        parent_dir = os.path.dirname(dir_path)
        _add_permission(dir_path, stat.S_IXOTH)
        _add_permission(parent_dir, stat.S_IXOTH)
        _add_permission(path, stat.S_IROTH)

    vm_name = params.get("main_vm")
    host_test_user = params.get("host_test_user")
    guest_user = params.get("guest_user")
    unprivileged_user = params.get('unprivileged_user')
    start_qemu_ga = ("no" == params.get("no_start_qemu_ga", "no"))
    status_error = ("yes" == params.get("status_error", "no"))
    uri = params.get("virsh_uri", None)
    acl_test = ("yes" == params.get("acl_test", "no"))

    libvirt_version.is_libvirt_feature_supported(params)

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()
    try:
        fail_patterns = []
        if status_error:
            if not start_qemu_ga:
                fail_patterns.append(r"QEMU guest agent is not connected")
            if acl_test:
                fail_patterns.append(r"error: access denied")

        vm.prepare_guest_agent(start=start_qemu_ga)

        pub_key_path = prepare_user_sshkeys(host_test_user)
        add_other_read_permission(pub_key_path)

        options = "--file %s" % pub_key_path
        res = virsh.set_user_sshkeys(vm_name, guest_user, options,
                                     ignore_status=True, debug=True,
                                     unprivileged_user=unprivileged_user,
                                     uri=uri)
        libvirt.check_result(res, expected_fails=fail_patterns)
    finally:
        session = vm.wait_for_login()
        session.cmd_output('userdel -r %s' % guest_user)
        session.close()
        process.run("userdel -r %s" % host_test_user)
        backup_xml.sync()
