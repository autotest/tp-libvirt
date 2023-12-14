from avocado.utils import process

from virttest import libvirt_remote
from virttest import libvirt_version
from virttest import remote
from virttest import utils_config
from virttest import utils_net

from virttest.utils_libvirt import libvirt_config
from virttest.utils_test import libvirt

from provider.migration import base_steps
from provider.migration import migration_base

qemu_conf_remote = None
remove_key_remote = None
ipv6_obj = None
NM_service = None


def setup_ipv4_env(params):
    """
    Setup ipv4 env on target host

    :param params: dictionary with the test parameter
    """
    ip_addr_suffix = params.get("ip_addr_suffix", "64")

    global NM_service
    # Check NetworkManager service, if running, will stop this service.
    if migration_base.check_NM(params, remote_host=True):
        NM_service = migration_base.get_NM_service(params, remote_host=True)
        if NM_service and NM_service.status():
            NM_service.stop()

    global ipv6_obj, ipv6_list
    # Delete all ipv6 addr on target host
    ipv6_obj = utils_net.IPv6Manager(params)
    ipv6_obj.session = ipv6_obj.get_session()
    runner = ipv6_obj.session.cmd_output
    ipv6_list = ipv6_obj.get_addr_list(runner=runner)
    for ipv6_addr in ipv6_list:
        utils_net.del_net_if_ip(ipv6_obj.server_ifname, (ipv6_addr + '/' + ip_addr_suffix), runner)
    ipv6_obj.close_session()


def cleanup_ipv4_env(params):
    """
    Cleanup ipv4 env on target host

    :param params: dictionary with the test parameter
    """
    ip_addr_suffix = params.get("ip_addr_suffix", "64")

    global NM_service
    # If NM_sercice exists, start NetworkManager service
    if NM_service and not NM_service.status():
        NM_service.start()
    global ipv6_obj, ipv6_list
    # Recover all ipv6 addr on target host
    ipv6_obj.session = ipv6_obj.get_session()
    runner = ipv6_obj.session.cmd_output
    for ipv6_addr in ipv6_list:
        utils_net.set_net_if_ip(ipv6_obj.server_ifname, (ipv6_addr + '/' + ip_addr_suffix), runner)
    ipv6_obj.close_session()


def run(test, params, env):
    """
    Test live migration with tcp transport.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """

    def setup_migration_host():
        """
        Setup for migration_host

        """
        set_migration_host = "yes" == params.get("set_migration_host", "no")
        src_hosts_dict = eval(params.get("src_hosts_conf", "{}"))
        qemu_conf_dest = params.get("qemu_conf_dest", "{}")
        server_params = {'server_ip': params.get("migrate_dest_host"),
                         'server_user': params.get("remote_user", "root"),
                         'server_pwd': params.get("migrate_dest_pwd")}
        qemu_conf_path = utils_config.LibvirtQemuConfig().conf_path

        test.log.info("Setup for migration_host.")
        if set_migration_host:
            qemu_conf_list = ["migration_host"]
            server_params['file_path'] = qemu_conf_path
            global remove_key_remote
            remove_key_remote = libvirt_config.remove_key_in_conf(qemu_conf_list,
                                                                  "qemu",
                                                                  remote_params=server_params,
                                                                  restart_libvirt=True)
            # backup /etc/hosts
            backup_hosts = "cp -f /etc/hosts /etc/hosts.bak"
            process.run(backup_hosts, shell=True)
            remote.run_remote_cmd(backup_hosts, params, ignore_status=False)
            if not utils_net.map_hostname_ipaddress(src_hosts_dict):
                test.log.error("Failed to set /etc/hosts on source host.")
        if qemu_conf_dest:
            global qemu_conf_remote
            qemu_conf_remote = libvirt_remote.update_remote_file(
                server_params, qemu_conf_dest, qemu_conf_path)
        migration_obj.setup_connection()

    def setup_migration_address():
        """
        Setup for migration_address case
        """
        qemu_conf_path = utils_config.LibvirtQemuConfig().conf_path
        qemu_conf_dest = params.get("qemu_conf_dest", "{}")
        default_qemu_conf = "yes" == params.get("default_qemu_conf", "no")
        ipv4_env_on_target = "yes" == params.get("ipv4_env_on_target", "no")

        test.log.info("Setup for migration_address case.")
        if default_qemu_conf:
            server_params = {'server_ip': params.get("migrate_dest_host"),
                             'server_user': params.get("remote_user", "root"),
                             'server_pwd': params.get("migrate_dest_pwd"),
                             'file_path': qemu_conf_path}
            global remove_key_remote
            remove_key_remote = libvirt_config.remove_key_in_conf(["migration_address"],
                                                                  "qemu",
                                                                  remote_params=server_params,
                                                                  restart_libvirt=True)
        else:
            global qemu_conf_remote
            qemu_conf_remote = libvirt_remote.update_remote_file(params,
                                                                 qemu_conf_dest,
                                                                 qemu_conf_path)
        if ipv4_env_on_target:
            test.log.info("Set ipv4 env on target host.")
            setup_ipv4_env(params)
        migration_obj.setup_connection()

    def run_again_for_migration_completion():
        """
        Run migration again for migration_completion case

        """
        dest_uri = params.get("virsh_migrate_desturi")

        vm.connect_uri = dest_uri
        if vm.is_alive():
            vm.destroy()
        vm.connect_uri = migration_obj.src_uri
        if not vm.is_alive():
            vm.start()
        vm.wait_for_login().close()

        migration_obj.run_migration_again()

    def cleanup_migration_host():
        """
        Cleanup for migration_host

        """
        set_migration_host = "yes" == params.get("set_migration_host", "no")
        test.log.info("Cleanup for migration_host.")
        migration_obj.cleanup_connection()
        global qemu_conf_remote
        if qemu_conf_remote:
            test.log.info("Recover remote qemu configurations")
            del qemu_conf_remote
        # Restore /etc/hosts
        if set_migration_host:
            restore_hosts = "mv -f /etc/hosts.bak /etc/hosts"
            process.run(restore_hosts, shell=True)
            remote.run_remote_cmd(restore_hosts, params, ignore_status=False)
            global remove_key_remote
            if remove_key_remote:
                del remove_key_remote
        libvirt.remotely_control_libvirtd(server_ip, server_user, server_pwd, "restart")

    def cleanup_migration_address():
        """
        Cleanup for migration_address case
        """
        ipv4_env_on_target = "yes" == params.get("ipv4_env_on_target", "no")

        test.log.info("Cleanup for migration_address case.")
        migration_obj.cleanup_connection()
        global qemu_conf_remote
        if qemu_conf_remote:
            test.log.info("Recover remote qemu configurations")
            del qemu_conf_remote
        global remove_key_remote
        if remove_key_remote:
            del remove_key_remote
        libvirt.remotely_control_libvirtd(server_ip, server_user, server_pwd, "restart")
        if ipv4_env_on_target:
            cleanup_ipv4_env(params)

    libvirt_version.is_libvirt_feature_supported(params)

    test_case = params.get('test_case', '')
    vm_name = params.get("migrate_main_vm")
    migrate_again = "yes" == params.get("migrate_again", "no")
    server_ip = params.get("server_ip")
    server_user = params.get("server_user", "root")
    server_pwd = params.get("server_pwd")

    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)
    setup_test = eval("setup_%s" % test_case) if "setup_%s" % test_case in \
        locals() else migration_obj.setup_connection
    run_migration_again = eval("run_again_for_%s" % test_case) if "run_again_for_%s" % test_case in \
        locals() else migration_obj.run_migration_again
    cleanup_test = eval("cleanup_%s" % test_case) if "cleanup_%s" % test_case in \
        locals() else migration_obj.cleanup_connection

    try:
        setup_test()
        migration_obj.run_migration()
        if migrate_again:
            run_migration_again()
        migration_obj.verify_default()
    finally:
        cleanup_test()
