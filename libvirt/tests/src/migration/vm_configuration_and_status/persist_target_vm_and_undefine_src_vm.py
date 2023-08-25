from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.graphics import Graphics
from virttest.utils_test import libvirt

from provider.migration import base_steps


def setup_vm(vm, vm_name, params):
    """
    Setup vm status

    :param vm: vm object
    :param vm_name: vm_name
    :param params: Dictionary with the test parameter
    """
    src_config = params.get("src_config")

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    if src_config == "transient":
        virsh.destroy(vm_name, debug=True)
        virsh.undefine(vm_name, options='--nvram', debug=True, ignore_status=False)
        virsh.create(vmxml.xml, debug=True, ignore_status=False)
    else:
        if not vm.is_alive():
            vm.start()
            vm.wait_for_login().close()
        vmxml.remove_all_device_by_type('graphics')
        graphic = Graphics(type_name='vnc')
        graphic.autoport = "no"
        graphic.port = "6000"
        virsh.update_device(vm_name, graphic.xml, flagstr='--config', debug=True)


def check_autoport(params, vm_name):
    """
    Check autoport in xml

    :param params: Dictionary with the test parameter
    :param vm_name: vm_name
    """
    desturi = params.get("virsh_migrate_desturi")
    persistent_option = params.get("persistent_option")
    src_config = params.get("src_config")

    active_xml = virsh.dumpxml(vm_name, uri=desturi, debug=True)
    libvirt.check_result(active_xml, expected_match="autoport='yes'")

    if persistent_option:
        virsh.destroy(vm_name, uri=desturi, debug=True)
        inactive_xml = virsh.dumpxml(vm_name, uri=desturi, debug=True)
        if src_config == "transient":
            libvirt.check_result(inactive_xml, expected_match="autoport='yes'")
        else:
            libvirt.check_result(inactive_xml, expected_match="autoport='no'")


def run(test, params, env):
    """
    Test VM live migration - persist target vm and undefine src vm.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    def setup_test():
        """
        Setup steps
        """
        persistent_option = params.get("persistent_option")
        extra = params.get("virsh_migrate_extra")
        src_config = params.get("src_config")

        test.log.info("Setup steps.")
        if persistent_option:
            extra = "%s %s" % (extra, persistent_option)
            params.update({"virsh_migrate_extra": extra})

        migration_obj.setup_connection()
        setup_vm(vm, vm_name, params)

    def verify_test():
        """
        Verify steps
        """
        desturi = params.get("virsh_migrate_desturi")
        persistent_option = params.get("persistent_option")
        src_config = params.get("src_config")
        extra = params.get("virsh_migrate_extra")

        test.log.info("Verify step.")
        if "undefinesource" in extra or src_config == "transient":
            if virsh.domain_exists(vm_name):
                test.fail("The domain on source host is found, but expected not.")

        if persistent_option:
            dominfo = virsh.dominfo(vm_name, ignore_status=True, debug=True, uri=desturi)
            libvirt.check_result(dominfo, expected_match="Persistent:     yes")

        check_autoport(params, vm_name)

    vm_name = params.get("migrate_main_vm")
    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)

    try:
        setup_test()
        migration_obj.run_migration()
        verify_test()
    finally:
        migration_obj.cleanup_connection()
