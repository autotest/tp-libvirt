from avocado.utils import process

from virttest import virsh
from virttest import libvirt_version
from virttest import ssh_key

from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Test get/set-user-sshkeys command, make sure that all supported options work well
    """

    def add_guest_user(name, passwd):
        """
        Added a user account in guest

        :param name: user name
        :param passwd: password of user account
        """
        try:
            session = vm.wait_for_login()
            session.cmd_output('useradd %s' % name)
        finally:
            session.close()
        virsh.set_user_password(vm_name, name, passwd, debug=True)

    def check_user_sshkeys(user, public_key_str):
        """
        Check whether the public_key_str is the authorized key of user in guest

        :param user: the user name
        :param public_key_str: public key string to be checked
        :return: bool of whether the key is authorized and
                 all the authorized key of user
        """
        authorized_keys = virsh.get_user_sshkeys(vm_name, user).stdout.strip()
        return (public_key_str.strip() in authorized_keys, authorized_keys)

    def check_ssh_conn(host_test_user, guest_user, vm_ip):
        """
        Check whether ssh connection to guest user from host user account can be
        seccussfull

        :param user: ssh connection from which host user
        :param guest_user: ssh connection to which guest user account
        :param vm_ip: the ip address of the guest
        :return: whether ssh connection is failed
        """
        if host_test_user == "root":
            key_path = '/root/.ssh/id_rsa'
        else:
            key_path = '/home/%s/.ssh/id_rsa' % host_test_user
        cmd = 'su - %s -c "ssh -i %s -o StrictHostKeyChecking=no %s@%s"' % (
                host_test_user, key_path, guest_user, vm_ip)
        conn = process.run(cmd, shell=True, verbose=True, ignore_status=True)
        return conn.exit_status == 0

    vm_name = params.get("main_vm")
    option = params.get("option", "")
    host_pre_added_user = params.get("host_pre_added_user")
    host_test_user = params.get("host_test_user")
    guest_user = params.get("guest_user")
    guest_user_passwd = params.get("guest_user_passwd")
    key_type = params.get("key_type", "rsa")
    key_file = ("yes" == params.get("key_file", "yes"))
    status_error = ("yes" == params.get("status_error", "no"))

    if not libvirt_version.version_compare(6, 10, 0):
        test.cancel("get/set-user-sshkeys command are not supported "
                    "before version libvirt-6.0.0 ")

    vm = env.get_vm(vm_name)
    vm_ip = vm.wait_for_get_address(0, timeout=60)

    try:
        #Prepare env
        add_guest_user(guest_user, guest_user_passwd)
        process.run('useradd %s' % host_pre_added_user, shell=True)
        ssh_key.setup_ssh_key(vm_ip, guest_user, guest_user_passwd,
                              client_user=host_pre_added_user)
        pub_key_path = '/home/%s/.ssh/id_rsa.pub' % host_pre_added_user
        with open(pub_key_path, 'r') as pkp:
            pre_added_key = pkp.read().strip()

        process.run('useradd %s' % host_test_user, shell=True)
        pub_key = ssh_key.get_public_key(client_user=host_test_user)
        pub_key_path = "/home/%s/.ssh/id_rsa.pub" % host_test_user
        if not key_file:
            pub_key_path = None

        if "--remove" in option:
            options = "--remove %s" % pub_key_path
        elif "--reset" in option:
            if key_file:
                options = "--reset %s" % pub_key_path
            else:
                options = "--reset"
        else:
            options = pub_key_path

        result = virsh.set_user_sshkeys(vm_name, guest_user, options,
                                        ignore_status=True, debug=True)
        libvirt.check_result(result, expected_fails=[],
                             any_error=status_error)

        key_authorized, user_sshkeys = check_user_sshkeys(guest_user, pub_key)
        if not option:
            if not key_authorized:
                test.fail("The key should be added, please check the user's "
                          "authorized keys %s" % user_sshkeys)
            if not check_ssh_conn(host_test_user, guest_user, vm_ip):
                test.fail("ssh connection to guest should be sucessful")
        if "--remove" in option:
            pre_added_key_authorized, _ = check_user_sshkeys(guest_user,
                                                             pre_added_key)
            if key_authorized or not pre_added_key_authorized:
                test.fail("The key should be removed, please check the user's "
                          "authorized keys %s" % user_sshkeys)
            if check_ssh_conn(host_test_user, guest_user, vm_ip):
                test.fail("ssh connection to guest should fail")
        if "--reset" in option:
            if key_file:
                pre_added_key_authorized, _ = check_user_sshkeys(guest_user,
                                                                 pre_added_key)
                if not key_authorized or pre_added_key_authorized:
                    test.fail("The key in key_file should be added, and other keys "
                              "should be deleted. please check the user's"
                              "authorized keys %s" % user_sshkeys)
                if not check_ssh_conn(host_test_user, guest_user, vm_ip):
                    test.fail("ssh connection to guest should be sucessful")
            else:
                if user_sshkeys:
                    test.fail("All keys should be removed, please check the user's "
                              "authorized keys %s" % user_sshkeys)
                if check_ssh_conn(host_test_user, guest_user, vm_ip):
                    test.fail("ssh connection to guest should fail")
    finally:
        session = vm.wait_for_login()
        session.cmd_output('userdel -r %s' % guest_user)
        session.close()
        process.run("userdel -r %s" % host_pre_added_user)
        process.run("userdel -r %s" % host_test_user)
