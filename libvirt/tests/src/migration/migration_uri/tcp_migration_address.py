from virttest import libvirt_remote
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
    Test live migration with tcp transport - migration_address.

    """

    def setup_test():
        """
        Setup steps

        """
        qemu_conf_path = utils_config.LibvirtQemuConfig().conf_path
        qemu_conf_dest = params.get("qemu_conf_dest", "{}")
        default_qemu_conf = "yes" == params.get("default_qemu_conf", "no")
        ipv4_env_on_target = "yes" == params.get("ipv4_env_on_target", "no")

        test.log.info("Setup steps.")
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

    def cleanup_test():
        """
        Cleanup steps

        """
        ipv4_env_on_target = "yes" == params.get("ipv4_env_on_target", "no")

        test.log.info("Cleanup steps.")
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

    vm_name = params.get("migrate_main_vm")
    server_ip = params.get("server_ip")
    server_user = params.get("server_user", "root")
    server_pwd = params.get("server_pwd")

    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)

    try:
        setup_test()
        migration_obj.run_migration()
        migration_obj.verify_default()
    finally:
        cleanup_test()
