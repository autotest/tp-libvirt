import logging

from virttest import libvirt_vm
from virttest import migration

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_network  # pylint: disable=W0611
from virttest.utils_test import libvirt


def run(test, params, env):
    """
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
    stress_package = params.get("stress_package")
    action_during_mig = params.get("action_during_mig")
    if action_during_mig:
        action_during_mig = eval(action_during_mig)

    # For safety reasons, we'd better back up  xmlfile.
    new_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    orig_config_xml = new_xml.copy()

    try:
        libvirt.set_vm_disk(vm, params)
        if not vm.is_alive():
            vm.start()

        logging.debug("Guest xml after starting:\n%s",
                      vm_xml.VMXML.new_from_dumpxml(vm_name))

        vm.wait_for_login().close()

        if stress_package:
            migration_test.run_stress_in_vm(vm, params)

        # Execute migration process
        logging.info("Starting migration...")
        vms = [vm]
        migration_test.do_migration(vms, None, dest_uri, 'orderly',
                                    options, thread_timeout=900,
                                    ignore_status=True,
                                    virsh_opt=virsh_options,
                                    extra_opts=extra,
                                    func=action_during_mig,
                                    **extra_args)
    finally:
        logging.info("Recover test environment")
        vm.connect_uri = bk_uri
        # Clean VM on destination and source
        migration_test.cleanup_vm(vm, dest_uri)

        orig_config_xml.sync()
