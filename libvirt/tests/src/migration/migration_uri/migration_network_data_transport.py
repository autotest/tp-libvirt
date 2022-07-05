from virttest import libvirt_version

from provider.migration import base_steps


def run(test, params, env):
    """
    Test live migration with UNIX/Tunnelled transport.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """

    libvirt_version.is_libvirt_feature_supported(params)

    vm_name = params.get("migrate_main_vm")
    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)

    try:
        migration_obj.setup_connection()
        migration_obj.run_migration()
        migration_obj.verify_default()
    finally:
        migration_obj.cleanup_connection()
