from avocado.utils import process

from virttest import remote

from provider.migration import base_steps


def run(test, params, env):
    """
    Test live migration with copy storage - network data transport - TLS.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    def setup_wrong_cert_configuration():
        """
        Setup for wrong cert configuration

        """
        cert_configuration = params.get("cert_configuration", '')
        custom_pki_path = params.get("custom_pki_path")
        cert_path = params.get("cert_path")

        test.log.info("Setup for wrong cert configuration.")
        migration_obj.setup_connection()
        cmd = "rm -f %s" % cert_path
        if cert_configuration == "no_client_cert_on_src":
            process.run(cmd, shell=True)
        elif cert_configuration == "no_server_cert_on_target":
            remote.run_remote_cmd(cmd, params, ignore_status=False)

    def cleanup_test():
        """
        Cleanup steps

        """
        migration_obj.cleanup_connection()
        base_steps.cleanup_disks_remote(params, vm)

    test_case = params.get('test_case', '')
    migrate_again = "yes" == params.get("migrate_again", "no")
    vm_name = params.get("migrate_main_vm")

    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)
    setup_test = eval("setup_%s" % test_case) if "setup_%s" % test_case in \
        locals() else migration_obj.setup_connection

    try:
        setup_test()
        base_steps.prepare_disks_remote(params, vm)
        migration_obj.run_migration()
        if migrate_again:
            migration_obj.run_migration_again()
        migration_obj.verify_default()
    finally:
        cleanup_test()
