import shutil

from virttest import libvirt_version

from virttest.libvirt_xml import vm_xml

from provider.migration import base_steps
from provider.migration import migration_base


def run(test, params, env):
    """
    This case initiates postcopy migration, then abort it, then make I/O error
    in vm, then recover postcopy migration.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    def do_io_error():
        """
        Make I/O error in vm

        """
        vm_session = params.get("vm_session")

        test.log.debug("Do I/O Error.")
        shutil.chown(disk_source, "root", "root")
        vm_session.cmd("echo 'do disk I/O error test' > /tmp/disk_io_error_test")
        vm_session.close()

    def verify_test():
        """
        Verify steps

        """
        test.log.debug("Verify steps.")
        migration_base.check_event_output(params, test, remote_virsh_session=remote_virsh_session)
        migration_obj.verify_default()

    vm_name = params.get("migrate_main_vm")
    migrate_again = "yes" == params.get("migrate_again", "no")

    remote_virsh_session = None

    libvirt_version.is_libvirt_feature_supported(params)
    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)
    sourcelist = vm_xml.VMXML.get_disk_source(vm_name)
    disk_source = sourcelist[0].find('source').get('file')
    params.update({"migration_obj": migration_obj})

    try:
        migration_obj.setup_connection()
        _, remote_virsh_session = migration_base.monitor_event(params)
        migration_obj.run_migration()
        do_io_error()
        if migrate_again:
            migration_obj.run_migration_again()
        verify_test()
    finally:
        migration_obj.cleanup_connection()
