from virttest.libvirt_xml import vm_xml

from provider.migration import base_steps
from provider.guest_os_booting import guest_os_booting_base


def run(test, params, env):
    """
    Migrate vm with boot elements.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    def update_vm_xml(vm, params):
        """
        Update VM xml

        :param vm: VM object
        :param params: Dictionary with the test parameters
        """
        iface_dict = eval(params.get("iface_dict", "{}"))
        os_attrs_boots = eval(params.get('os_attrs_boots', '[]'))
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm.name)

        if os_attrs_boots:
            os_attrs = {'boots': os_attrs_boots}
            vmxml.setup_attrs(os=os_attrs)
        else:
            vm_os = vmxml.os
            vm_os.del_boots()
            vmxml.os = vm_os
            xml_devices = vmxml.devices
            if iface_dict:
                iface_obj = xml_devices.by_device_tag('interface')[0]
                iface_obj.setup_attrs(**iface_dict)
            vmxml.devices = xml_devices
            disk_attrs = xml_devices.by_device_tag('disk')[0].fetch_attrs()
            vmxml.set_boot_order_by_target_dev(
                disk_attrs['target']['dev'], params.get('disk_boot_idx'))
        vmxml.xmltreefile.write()
        test.log.debug(f"vmxml after updating: {vmxml}")
        vmxml.sync()

    def setup_test():
        """
        Test setup
        """
        update_vm_xml(vm, params)
        migration_obj.setup_default()

    def verify_test_again():
        """
        Test verify
        """
        migrate_vm_back = "yes" == params.get("migrate_vm_back", "yes")
        if not migrate_vm_back:
            return
        test.log.info("Verify test again.")
        dargs = {"check_disk_on_dest": "no"}
        migration_obj.migration_test.post_migration_check([vm], dargs)

    vm_name = guest_os_booting_base.get_vm(params)
    vm = env.get_vm(vm_name)

    migration_obj = base_steps.MigrationBase(test, vm, params)

    try:
        setup_test()
        test.log.info("TEST_STEP: Migrate the VM to the target host.")
        migration_obj.run_migration()
        migration_obj.verify_default()

        test.log.info("TEST_STEP: Migrate back the VM to the source host.")
        migration_obj.run_migration_back()
        verify_test_again()

    finally:
        migration_obj.cleanup_default()
