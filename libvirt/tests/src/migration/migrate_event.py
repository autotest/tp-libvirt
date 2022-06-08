import aexpect

import logging as log

from virttest import libvirt_vm
from virttest import migration

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt

from provider.migration import migration_base


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Run the test to check events when migration

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    vm_name = params.get("migrate_main_vm")
    vm = env.get_vm(vm_name)
    vm.verify_alive()
    bk_uri = vm.connect_uri

    migration_test = migration.MigrationTest()
    migration_test.check_parameters(params)
    extra_args = migration_test.update_virsh_migrate_extra_args(params)

    extra = params.get("virsh_migrate_extra")
    postcopy_options = params.get("postcopy_options")
    if postcopy_options:
        extra = "%s %s" % (extra, postcopy_options)
    params["virsh_migrate_desturi"] = libvirt_vm.complete_uri(
        params.get("migrate_dest_host"))
    dest_uri = params.get("virsh_migrate_desturi")
    options = params.get("virsh_migrate_options",
                         "--live --p2p --persistent --verbose")
    virsh_options = params.get("virsh_options", "")
    action_during_mig = params.get("action_during_mig")
    migrate_speed = params.get("migrate_speed")

    virsh_session = None
    remote_session = None

    if action_during_mig:
        action_during_mig = migration_base.parse_funcs(action_during_mig,
                                                       test, params)

    # For safety reasons, we'd better back up  xmlfile.
    new_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    orig_config_xml = new_xml.copy()

    try:
        # Change the disk of the vm
        libvirt.set_vm_disk(vm, params)

        if not vm.is_alive():
            vm.start()

        logging.debug("Guest xml after starting:\n%s",
                      vm_xml.VMXML.new_from_dumpxml(vm_name))

        vm.wait_for_login().close()

        if migrate_speed:
            mode = 'both' if '--postcopy' in postcopy_options else 'precopy'
            migration_test.control_migrate_speed(vm_name,
                                                 int(migrate_speed),
                                                 mode)
        # Monitor event on source/target host
        virsh_session, remote_virsh_session = migration_base.monitor_event(params)

        # Execute migration process
        migration_base.do_migration(vm, migration_test, None, dest_uri,
                                    options, virsh_options, extra,
                                    action_during_mig,
                                    extra_args)

        func_returns = dict(migration_test.func_ret)
        migration_test.func_ret.clear()
        logging.debug("Migration returns function results:%s", func_returns)
        aexpect.kill_tail_threads()

        # Check event output
        migration_base.check_event_output(params, test, virsh_session, remote_virsh_session)

        if int(migration_test.ret.exit_status) == 0:
            migration_test.post_migration_check([vm], params, dest_uri=dest_uri)
    finally:
        logging.info("Recover test environment")
        vm.connect_uri = bk_uri
        # Clean VM on destination and source
        migration_test.cleanup_vm(vm, dest_uri)

        orig_config_xml.sync()
