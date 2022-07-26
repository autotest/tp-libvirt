from virttest import libvirt_remote
from virttest import libvirt_version

from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_config

from provider.migration import base_steps


def update_qemu_conf(params, test, remote_obj, local_obj):
    """
    Update remote configure file

    :param params: Dictionary with the test parameter
    :param test: test object
    :param remote_obj: remote qemu conf object
    :param local_obj: local qemu conf object
    """
    default_qemu_conf = "yes" == params.get("default_qemu_conf", "no")
    qemu_conf_path = params.get("qemu_conf_path")
    qemu_conf_dest = params.get("qemu_conf_dest", "{}")
    qemu_conf_src = eval(params.get("qemu_conf_src", "{}"))

    if default_qemu_conf:
        value_list = ["migrate_tls_force"]
        # Setup migrate_tls_force default value on remote
        params['file_path'] = qemu_conf_path
        remote_obj.append(libvirt_config.remove_key_in_conf(value_list,
                                                            "qemu",
                                                            remote_params=params))
        # Setup migrate_tls_force default value on local
        local_obj.append(libvirt_config.remove_key_in_conf(value_list, "qemu"))
    else:
        # Update remote qemu conf
        if qemu_conf_dest:
            remote_obj.append(libvirt_remote.update_remote_file(params,
                                                                qemu_conf_dest,
                                                                qemu_conf_path))
        # Update local qemu conf
        if qemu_conf_src:
            local_obj.append(libvirt.customize_libvirt_config(qemu_conf_src,
                                                              "qemu",
                                                              remote_host=False,
                                                              extra_params=params))


def run(test, params, env):
    """
    Test live migration with TLS transport.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    def setup_migrate_tls_force():
        """
        Setup for migrate_tls_force

        """
        test.log.info("Setup for migrate_tls_force.")

        update_qemu_conf(params, test, remote_obj, local_obj)
        migration_obj.setup_connection()

    def cleanup_migrate_tls_force():
        """
        Cleanup steps for migrate_tls_force

        """
        test.log.info("Cleanup steps for migrate_tls_force.")
        migration_obj.cleanup_connection()
        if remote_obj:
            for obj in remote_obj:
                del obj
        if local_obj:
            for obj in local_obj:
                libvirt.customize_libvirt_config(None,
                                                 config_type="qemu",
                                                 remote_host=False,
                                                 is_recover=True,
                                                 extra_params=params,
                                                 config_object=obj)

    libvirt_version.is_libvirt_feature_supported(params)

    test_case = params.get('test_case', '')
    migrate_again = "yes" == params.get("migrate_again", "no")
    vm_name = params.get("migrate_main_vm")

    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)
    setup_test = eval("setup_%s" % test_case) if "setup_%s" % test_case in \
        locals() else migration_obj.setup_connection
    cleanup_test = eval("cleanup_%s" % test_case) if "cleanup_%s" % test_case in \
        locals() else migration_obj.cleanup_connection

    remote_obj = []
    local_obj = []

    try:
        setup_test()
        migration_obj.run_migration()
        if migrate_again:
            migration_obj.run_migration_again()
        migration_obj.verify_default()
    finally:
        cleanup_test()
