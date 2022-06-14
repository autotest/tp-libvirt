from virttest import migration

from virttest.libvirt_xml import vm_xml

from provider.migration import migration_base
from provider.migration import base_steps


def run(test, params, env):
    """
    Test live migration with TCP transport.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """

    def setup_tcp(vm, params, test):
        """
        Setup for TCP transport

        :param vm: vm object
        :param params: Dictionary with the test parameter
        :param test: test object
        """
        test.log.info("Setup for TCP transport.")
        dest_uri = params.get("virsh_migrate_desturi")
        uri_port = params.get("uri_port")
        if uri_port:
            migration_test.migrate_pre_setup(dest_uri, params, cleanup=False, ports=uri_port)

        conn_obj_list.append(migration_base.setup_conn_obj('tcp', params, test))
        base_steps.setup_default(vm, params, test)

    def cleanup_tcp(migration_test, vm, dest_uri, src_uri, orig_config_xml, test):
        """
        Cleanup steps for TCP transport

        :param vm: vm object
        :param migration_test: MigrationTest object
        :param dest_uri: target uri
        :param src_uri: backup source uri
        :param orig_config_xml: back up xmlfile
        :param conn_obj_list: connection object list, default value is None
        :param test: test object
        """
        test.log.info("Cleanup steps for TCP transport.")
        base_steps.cleanup_default(migration_test, vm, dest_uri, src_uri, orig_config_xml, test)
        migration_base.cleanup_conn_obj(conn_obj_list, test)

    dest_uri = params.get("virsh_migrate_desturi")
    test_case = params.get("test_case", "")
    setup_test = eval("setup_%s" % test_case) \
        if 'setup_%s' % test_case in locals() else base_steps.setup_default
    cleanup_test = eval("cleanup_%s" % test_case) \
        if 'cleanup_%s' % test_case in locals() else base_steps.cleanup_default

    migration_test = migration.MigrationTest()
    migration_test.check_parameters(params)

    conn_obj_list = []

    vm_name = params.get("migrate_main_vm")
    vm = env.get_vm(vm_name)
    src_uri = vm.connect_uri
    # For safety reasons, we'd better back up xmlfile.
    new_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    orig_config_xml = new_xml.copy()

    try:
        # Execute test
        setup_test(vm, params, test)
        base_steps.run_migration(vm, migration_test, params, test)
        base_steps.verify_default(vm, migration_test, src_uri, params, test)
    finally:
        cleanup_test(migration_test, vm, dest_uri, src_uri, orig_config_xml, test)
