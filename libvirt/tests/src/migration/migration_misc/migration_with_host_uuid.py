from virttest.utils_test import libvirt

from provider.migration import base_steps

local_obj = None


def run(test, params, env):
    """
    To verify that migration will fail if source host and target host have
    same host_uuid.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    def setup_test():
        """
        Setup steps for cases

        """
        test.log.info("Setup steps for cases.")
        libvirtd_conf = eval(params.get("libvirtd_conf"))

        local_obj = libvirt.customize_libvirt_config(libvirtd_conf,
                                                     remote_host=True,
                                                     extra_params=params)
        migration_obj.setup_connection()

    def cleanup_test():
        """
        Cleanup steps for cases

        """
        migration_obj.cleanup_connection()
        global local_obj
        if local_obj:
            libvirt.customize_libvirt_config(None,
                                             remote_host=True,
                                             is_recover=True,
                                             extra_params=params,
                                             config_object=local_obj)

    vm_name = params.get("migrate_main_vm")

    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)

    try:
        setup_test()
        migration_obj.run_migration()
        migration_obj.verify_default()
    finally:
        cleanup_test()
