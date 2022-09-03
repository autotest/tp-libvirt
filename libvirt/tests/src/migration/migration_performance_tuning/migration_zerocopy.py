from virttest import libvirt_version

from virttest.libvirt_xml import vm_xml

from virttest.utils_libvirt import libvirt_memory

from provider.migration import base_steps


def run(test, params, env):
    """
    Test live migration - zero-copy.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    def setup_with_memtune():
        """
        Setup with memtune

        """
        test.log.debug("Setup for with memtune.")
        hard_limit = params.get("hard_limit")
        if hard_limit:
            params.update({'expect_hard_limit': hard_limit})
        test.log.info("hard limit: %s", hard_limit)
        vm_attrs = eval(params.get('vm_attrs', '{}'))

        if check_hard_limit:
            old_hard_limit.append(libvirt_memory.get_qemu_process_memlock_hard_limit())

        if vm_attrs:
            vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
            vmxml.setup_attrs(**vm_attrs)
            vmxml.sync()
            test.log.info("Updated VM xml: %s", vm_xml.VMXML.new_from_dumpxml(vm_name))

        migration_obj.setup_connection()

    def setup_without_memtune():
        """
        Setup without memtune

        """
        test.log.debug("Setup for without memtune.")
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        cur_mem = vmxml.current_mem
        test.log.info("VM current memory: %s", cur_mem)
        params.update({'expect_hard_limit': cur_mem})

        if check_hard_limit:
            old_hard_limit.append(libvirt_memory.get_qemu_process_memlock_hard_limit())

        migration_obj.setup_connection()

    def migration_test_again():
        """
        Migration again

        """
        if check_hard_limit:
            if not vm.is_alive():
                vm.connect_uri = migration_obj.src_uri
                vm.start()
                vm.wait_for_login().close()
            new_hard_limit = libvirt_memory.get_qemu_process_memlock_hard_limit()
            test.log.debug("old hard limit: %s", old_hard_limit[0])
            test.log.debug("new hard limit: %s", new_hard_limit)
            if new_hard_limit != old_hard_limit[0]:
                test.fail("Check qemu process memlock hard limit failed.")

        migration_obj.run_migration_again()

    libvirt_version.is_libvirt_feature_supported(params)

    test_case = params.get('test_case', '')
    vm_name = params.get("migrate_main_vm")
    migrate_again = "yes" == params.get("migrate_again", "no")
    check_hard_limit = "yes" == params.get("check_hard_limit", "no")

    old_hard_limit = []

    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)
    setup_test = eval("setup_%s" % test_case) if "setup_%s" % test_case in \
        locals() else migration_obj.setup_connection

    try:
        setup_test()
        migration_obj.run_migration()
        if migrate_again:
            migration_test_again()
        migration_obj.verify_default()
    finally:
        migration_obj.cleanup_connection()
