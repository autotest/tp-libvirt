from virttest import virsh

from virttest.utils_test import libvirt

from provider.migration import base_steps


def run(test, params, env):
    """
    Test offline migration.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    def verify_test():
        """
        Verify steps

        """
        desturi = params.get("virsh_migrate_desturi")
        virsh_migrate_extra = params.get("virsh_migrate_extra")
        virsh_migrate_src_state = params.get("virsh_migrate_src_state")
        src_vm_persistency_state = params.get("src_vm_persistency_state")

        test.log.info("Verify steps.")
        if virsh_migrate_src_state and "failed to get domain" in virsh_migrate_src_state:
            func_returns = dict(migration_obj.migration_test.func_ret)
            migration_obj.migration_test.func_ret.clear()
            test.log.debug("Migration returns function results:%s", func_returns)
            if int(migration_obj.migration_test.ret.exit_status) == 0:
                migration_obj.migration_test.post_migration_check([vm], params,
                                                                  dest_uri=desturi)
                result = virsh.domstate(vm_name, extra="--reason", uri=migration_obj.src_uri, debug=True)
                libvirt.check_result(result, expected_fails=virsh_migrate_src_state)
        else:
            migration_obj.verify_default()

        if src_vm_persistency_state:
            src_vm_persistency = virsh.dom_list(options="--all --persistent", debug=True)
            if src_vm_persistency_state == "nonexist":
                if vm_name in src_vm_persistency.stdout.strip():
                    test.fail("%s should not exist." % vm_name)
            else:
                pat_str = "%s.*%s" % (vm_name, src_vm_persistency_state)
                libvirt.check_result(src_vm_persistency, expected_match=pat_str)

        dest_vm_xml = virsh.dumpxml(vm_name, extra="--migratable --inactive", debug=True, uri=desturi).stdout.strip()
        test.log.debug("dest vm xml: %s", dest_vm_xml)
        if dest_vm_xml != src_vm_xml:
            test.fail("Check source and target vm xml failed.")

    vm_name = params.get("migrate_main_vm")

    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)

    try:
        migration_obj.setup_connection()
        src_vm_xml = virsh.dumpxml(vm_name, extra="--migratable --inactive", debug=True).stdout.strip()
        test.log.debug("src vm xml: %s", src_vm_xml)
        migration_obj.run_migration()
        verify_test()
    finally:
        migration_obj.cleanup_connection()
