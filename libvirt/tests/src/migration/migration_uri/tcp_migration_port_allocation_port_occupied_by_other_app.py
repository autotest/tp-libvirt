from virttest import remote

from provider.migration import base_steps


def run(test, params, env):
    """
    To verify that when migration port is occupied by other app, libvirt will
    allocate the next available port to migration.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    vm_name = params.get("migrate_main_vm")
    occupy_port_cmd = params.get("occupy_port_cmd")
    server_ip = params.get("server_ip")
    server_user = params.get("server_user", "root")
    server_pwd = params.get("server_pwd")

    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)

    try:
        # Ensure the guest CPU is compatible with the destination host before
        # migration. When the source and destination CPUs differ, compute a
        # migratable baseline and apply it; the guest must be off to rewrite
        # its CPU, so it is (re)started below. No-op when the CPUs already
        # match and on aarch64.
        if not base_steps.check_cpu_for_mig(params):
            if vm.is_alive():
                vm.destroy()
            base_steps.sync_cpu_for_mig(params)

        migration_obj.setup_connection()
        runner_on_target = remote.RemoteRunner(host=server_ip,
                                               username=server_user,
                                               password=server_pwd)
        remote.run_remote_cmd(occupy_port_cmd, params, runner_on_target, ignore_status=False)
        migration_obj.run_migration()
        migration_obj.verify_default()
    finally:
        migration_obj.cleanup_connection()
        remote.run_remote_cmd("pkill -9 nc", params)
