import aexpect

from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml

from provider.migration import migration_base


def setup_default(vm, params, test):
    """
    Setup steps by default

    :param vm: vm object
    :param params: Dictionary with the test parameter
    :param test: test object
    """
    test.log.info("Setup steps by default.")
    libvirt.set_vm_disk(vm, params)
    if not vm.is_alive():
        vm.start()
        vm.wait_for_login().close()


def run_migration(vm, migration_test, params, test):
    """
    Execute migration from source host to target host

    :param vm: vm object
    :param migration_test: MigrationTest object
    :param params: Dictionary with the test parameter
    :param test: test object
    """
    virsh_options = params.get("virsh_options", "")
    options = params.get("virsh_migrate_options", "--live --verbose")
    extra_args = migration_test.update_virsh_migrate_extra_args(params)
    dest_uri = params.get("virsh_migrate_desturi")
    vm_name = params.get("migrate_main_vm")
    action_during_mig = params.get("action_during_mig")
    migrate_speed = params.get("migrate_speed")
    stress_package = params.get("stress_package")
    extra = params.get("virsh_migrate_extra")
    postcopy_options = params.get("postcopy_options")
    if postcopy_options:
        extra = "%s %s" % (extra, postcopy_options)

    # Check local guest network connection before migration
    migration_test.ping_vm(vm, params)
    test.log.debug("Guest xml after starting:\n%s",
                   vm_xml.VMXML.new_from_dumpxml(vm_name))

    if action_during_mig:
        action_during_mig = migration_base.parse_funcs(action_during_mig,
                                                       test, params)
    mode = 'both' if postcopy_options else 'precopy'
    if migrate_speed:
        migration_test.control_migrate_speed(vm_name, int(migrate_speed), mode)
    if stress_package:
        migration_test.run_stress_in_vm(vm, params)

    # Execute migration process
    migration_base.do_migration(vm, migration_test, None, dest_uri,
                                options, virsh_options, extra,
                                action_during_mig, extra_args)


def verify_default(vm, migration_test, src_uri, params, test):
    """
    Verify steps by default

    :param vm: vm object
    :param migration_test: MigrationTest object
    :param src_uri: source uri
    :param params: Dictionary with the test parameter
    :param test: test object
    """
    dest_uri = params.get("virsh_migrate_desturi")
    vm_name = params.get("migrate_main_vm")

    aexpect.kill_tail_threads()
    func_returns = dict(migration_test.func_ret)
    migration_test.func_ret.clear()
    test.log.debug("Migration returns function results:%s", func_returns)
    if int(migration_test.ret.exit_status) == 0:
        migration_test.post_migration_check([vm], params, dest_uri=dest_uri, src_uri=src_uri)


def cleanup_default(migration_test, vm, dest_uri, src_uri, orig_config_xml, test):
    """
    Cleanup steps by default

    :param vm: vm object
    :param migration_test: MigrationTest object
    :param dest_uri: target uri
    :param src_uri: source uri
    :param orig_config_xml: original xmlfile
    :param test: test object
    """
    test.log.debug("Recover test environment")
    vm.connect_uri = src_uri
    # Clean VM on destination and source
    migration_test.cleanup_vm(vm, dest_uri)
    orig_config_xml.sync()
