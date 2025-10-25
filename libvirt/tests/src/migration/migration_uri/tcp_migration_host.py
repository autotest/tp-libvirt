from avocado.utils import process

from virttest import libvirt_remote
from virttest import remote
from virttest import utils_config
from virttest import utils_net

from virttest.utils_libvirt import libvirt_config
from virttest.utils_test import libvirt

from provider.migration import base_steps

qemu_conf_remote = None
remove_key_remote = None


def run(test, params, env):
    """
    Test live migration with tcp transport - migration_host.

    """

    def setup_test():
        """
        Setup steps

        """
        set_migration_host = "yes" == params.get("set_migration_host", "no")
        src_hosts_dict = eval(params.get("src_hosts_conf", "{}"))
        qemu_conf_dest = params.get("qemu_conf_dest", "{}")
        server_params = {'server_ip': server_ip,
                         'server_user': server_user,
                         'server_pwd': server_pwd}
        qemu_conf_path = utils_config.LibvirtQemuConfig().conf_path

        test.log.info("Setup steps.")
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

    def cleanup_test():
        """
        Cleanup steps

        """
        set_migration_host = "yes" == params.get("set_migration_host", "no")
        test.log.info("Cleanup steps.")
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
