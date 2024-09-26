from virttest import libvirt_remote
from virttest import remote
from virttest import utils_config
from virttest import virsh

from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_config
from virttest.utils_libvirt import libvirt_network

from provider.migration import base_steps
from provider.migration import migration_base


def update_conf_on_src(params, test, local_obj):
    """
    Update configure file on source host

    :param params: Dictionary with the test parameters
    :param test: test object
    :param local_obj: local conf object
    """
    default_conf_on_src = eval(params.get("default_conf_on_src", "[]"))
    src_conf_type = params.get("src_conf_type")
    conf_on_src = eval(params.get("conf_on_src", "{}"))

    if default_conf_on_src:
        local_obj.append(libvirt_config.remove_key_in_conf(default_conf_on_src,
                                                           conf_type=src_conf_type,
                                                           restart_libvirt=True))

    if conf_on_src:
        local_obj.append(libvirt.customize_libvirt_config(conf_on_src,
                                                          config_type=src_conf_type,
                                                          remote_host=False,
                                                          extra_params=params))


def update_conf_on_target(params, test, remote_obj):
    """
    Update configure file on target host

    :param params: Dictionary with the test parameters
    :param test: test object
    :param remote_obj: remote conf object
    """
    default_conf_on_target = eval(params.get("default_conf_on_target", "[]"))
    target_conf_type = params.get("target_conf_type")
    conf_on_target = params.get("conf_on_target")

    if default_conf_on_target:
        file_path = utils_config.get_conf_obj(target_conf_type).conf_path
        params["file_path"] = file_path
        remote_obj.append(libvirt_config.remove_key_in_conf(default_conf_on_target,
                                                            conf_type=target_conf_type,
                                                            remote_params=params,
                                                            restart_libvirt=True))
    if conf_on_target:
        file_path = utils_config.get_conf_obj(target_conf_type).conf_path
        remote_obj.append(libvirt_remote.update_remote_file(params,
                                                            conf_on_target,
                                                            file_path))


def add_port_for_ssh(params):
    """
    Add new port for sshd

    :param params: Dictionary with the test parameters
    """
    new_ssh_port = params.get("new_ssh_port")
    server_ip = params.get("server_ip")
    server_user = params.get("server_user")
    server_pwd = params.get("server_pwd")

    remote_session = remote.remote_login("ssh", server_ip, "22",
                                         server_user, server_pwd,
                                         r'[$#%]')
    cmd = "echo 'Port 22' >> /etc/ssh/sshd_config; echo 'Port %s' >> /etc/ssh/sshd_config" % new_ssh_port
    remote_session.cmd(cmd)
    cmd = "semanage port -a -t ssh_port_t -p tcp %s" % new_ssh_port
    remote_session.cmd(cmd)
    cmd = "firewall-cmd --add-port=%s/tcp" % new_ssh_port
    remote_session.cmd(cmd)
    cmd = "systemctl restart sshd"
    remote_session.cmd(cmd)
    remote_session.close()


def del_port_for_ssh(params):
    """
    Delete new port for sshd

    :param params: Dictionary with the test parameters
    """
    new_ssh_port = params.get("new_ssh_port")
    server_ip = params.get("server_ip")
    server_user = params.get("server_user")
    server_pwd = params.get("server_pwd")

    remote_session = remote.remote_login("ssh", server_ip, "22",
                                         server_user, server_pwd,
                                         r'[$#%]')
    cmd = "sed -i '/^Port .*/d' /etc/ssh/sshd_config"
    remote_session.cmd(cmd)
    cmd = "firewall-cmd --remove-port=%s/tcp" % new_ssh_port
    remote_session.cmd(cmd)
    cmd = "systemctl restart sshd"
    remote_session.cmd(cmd)
    remote_session.close()


def run(test, params, env):
    """
    Test P2P migration with different keepalive configuration.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    def setup_test():
        """
        Setup steps

        """
        test.log.info("Setup steps.")
        new_ssh_port = params.get("new_ssh_port")

        if new_ssh_port:
            add_port_for_ssh(params)
        migration_obj.setup_connection()
        update_conf_on_src(params, test, local_obj)
        update_conf_on_target(params, test, remote_obj)

    def verify_test():
        """
        Verify steps

        """
        migrate_dest_state = params.get("migrate_dest_state")
        migrate_src_state = params.get("migrate_src_state")
        dest_uri = params.get("virsh_migrate_desturi")
        keepalive_timeout = params.get("keepalive_timeout")

        test.log.info("Verify steps.")
        if keepalive_timeout == "after_keepalive_timeout":
            libvirt_network.cleanup_firewall_rule(params)
            func_returns = dict(migration_obj.migration_test.func_ret)
            migration_obj.migration_test.func_ret.clear()
            test.log.debug("Migration returns function results:%s", func_returns)
            if int(migration_obj.migration_test.ret.exit_status) == 0:
                test.fail("Expected failed, but migrate successfully.")
            if not libvirt.check_vm_state(vm.name, migrate_src_state, uri=migration_obj.src_uri):
                test.faile("Check vm state on source host fail.")
            dest_vm_list = virsh.dom_list(options="--all --persistent", debug=True, uri=dest_uri)
            if migrate_dest_state == "nonexist":
                if vm_name in dest_vm_list.stdout.strip():
                    test.fail("%s should not exist." % vm_name)
        else:
            migration_obj.verify_default()

    def cleanup_test():
        """
        Cleanup steps

        """
        test.log.info("Cleanup steps.")
        src_conf_type = params.get("src_conf_type")
        new_ssh_port = params.get("new_ssh_port")
        dest_uri = params.get("virsh_migrate_desturi")

        libvirt_network.cleanup_firewall_rule(params)
        if new_ssh_port:
            if virsh.is_alive(vm_name, uri=dest_uri):
                virsh.destroy(vm_name, uri=dest_uri)
            del_port_for_ssh(params)
        migration_obj.cleanup_connection()
        if remote_obj:
            for obj in remote_obj:
                del obj
        if local_obj:
            for obj in local_obj:
                libvirt.customize_libvirt_config(None,
                                                 config_type=src_conf_type,
                                                 remote_host=False,
                                                 is_recover=True,
                                                 extra_params=params,
                                                 config_object=obj)

    test_case = params.get('test_case', '')
    migrate_again = "yes" == params.get("migrate_again", "no")
    vm_name = params.get("migrate_main_vm")

    virsh_session = None
    remote_virsh_session = None

    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)

    remote_obj = []
    local_obj = []

    try:
        setup_test()
        if test_case != "src_and_dest_keepalive_disabled":
            # Monitor event on source/target host
            virsh_session, remote_virsh_session = migration_base.monitor_event(params)
        migration_obj.run_migration()
        verify_test()
        if test_case != "src_and_dest_keepalive_disabled":
            migration_base.check_event_output(params, test, virsh_session, remote_virsh_session)
        if migrate_again:
            migration_obj.run_migration_again()
            migration_obj.verify_default()
    finally:
        cleanup_test()
