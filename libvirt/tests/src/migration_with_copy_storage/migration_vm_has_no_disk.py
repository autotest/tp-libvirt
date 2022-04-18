import time

from virttest import virsh

from virttest.libvirt_xml import vm_xml

from provider.migration import base_steps
from provider.migration import migration_base


def run(test, params, env):
    """
    Test migration with copy storage - vm has no disk.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """

    def setup_test():
        """
        Setup for vm has no disk

        """
        transport_type = params.get("transport_type")
        migrate_desturi_port = params.get("migrate_desturi_port")

        test.log.info("Setup copy storage migration with no disk.")
        if transport_type:
            migration_obj.conn_list.append(migration_base.setup_conn_obj(transport_type, params, test))

        if migrate_desturi_port:
            migration_obj.remote_add_or_remove_port(migrate_desturi_port)

        new_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        disks = new_xml.get_devices(device_type="disk")
        for disk in disks:
            new_xml.del_device(disk)
        new_xml.sync()

        try:
            if not vm.is_alive():
                virsh.start(vm_name, debug=True, ignore_status=False)
                time.sleep(10)
        except Exception as err:
            test.log.error(err)

    vm_name = params.get("migrate_main_vm")

    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)

    try:
        setup_test()
        migration_obj.run_migration()
        migration_obj.verify_default()
    finally:
        migration_obj.cleanup_connection()
