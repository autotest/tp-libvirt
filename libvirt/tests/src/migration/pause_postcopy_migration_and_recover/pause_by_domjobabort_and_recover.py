from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt

from provider.migration import base_steps
from provider.migration import migration_base


def run(test, params, env):
    """
    Test postcopy migration, then pause migration by "virsh domjobabort --postcopy", then recover migration.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    def setup_persistent():
        """
        Setup for persistent option

        """
        test.log.info("Setup for persistent option.")
        extra = params.get("virsh_migrate_extra")
        update_persist_title = params.get("update_persist_title")
        tmp_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name, "--security-info")
        tmp_xml.title = update_persist_title
        extra = "%s --persistent --persistent-xml=%s" % (extra, tmp_xml.xml)
        params.update({"virsh_migrate_extra": extra})
        migration_obj.setup_connection()

    def setup_xml():
        """
        Setup for xml option

        """
        test.log.info("Setup for xml option.")
        extra = params.get("virsh_migrate_extra")
        update_xml_title = params.get("update_xml_title")

        migration_obj.setup_connection()
        tmp_xml = vm_xml.VMXML.new_from_dumpxml(vm_name, "--security-info --migratable")
        tmp_xml.title = update_xml_title
        extra = "%s --xml=%s" % (extra, tmp_xml.xml)
        params.update({"virsh_migrate_extra": extra})

    def verify_test():
        """
        Verify steps

        """
        dest_uri = params.get("virsh_migrate_desturi")
        dominfo_check = params.get("dominfo_check")
        migration_options = params.get("migration_options")
        dname_value = params.get("dname_value")

        test.log.info("Verify for pause_by_domjobabort_and_recover.")
        # check domain persistence
        if dominfo_check:
            if migration_options == "dname":
                dominfo = virsh.dominfo(dname_value, ignore_status=True, debug=True, uri=dest_uri)
            else:
                dominfo = virsh.dominfo(vm_name, ignore_status=True, debug=True, uri=dest_uri)
            libvirt.check_result(dominfo, expected_match=dominfo_check)

        # check qemu-guest-agent command
        if migration_options == "dname":
            result = virsh.domtime(dname_value, ignore_status=True, debug=True, uri=dest_uri)
        else:
            result = virsh.domtime(vm_name, ignore_status=True, debug=True, uri=dest_uri)
        libvirt.check_exit_status(result)

        if migration_options == "undefinesource":
            virsh_migrate_src_state = params.get("virsh_migrate_src_state")
            func_returns = dict(migration_obj.migration_test.func_ret)
            migration_obj.migration_test.func_ret.clear()
            test.log.debug("Migration returns function results:%s", func_returns)
            if int(migration_obj.migration_test.ret.exit_status) == 0:
                migration_obj.migration_test.post_migration_check([vm], params,
                                                                  dest_uri=dest_uri)
                result = virsh.domstate(vm_name, extra="--reason", uri=migration_obj.src_uri, debug=True)
                libvirt.check_result(result, expected_fails=virsh_migrate_src_state)
        elif migration_options == "dname":
            virsh_migrate_dest_state = params.get("virsh_migrate_dest_state")
            func_returns = dict(migration_obj.migration_test.func_ret)
            migration_obj.migration_test.func_ret.clear()
            test.log.debug("Migration returns function results:%s", func_returns)
            if int(migration_obj.migration_test.ret.exit_status) == 0:
                migration_obj.migration_test.post_migration_check([vm], params, dest_uri=dest_uri)
                result = virsh.domstate(vm_name, extra="--reason", uri=migration_obj.src_uri, debug=True)
                libvirt.check_result(result)
                result = virsh.domstate(dname_value, extra="--reason", uri=dest_uri, debug=True)
                libvirt.check_result(result)
        else:
            migration_obj.verify_default()

        # Check event output
        migration_base.check_event_output(params, test, virsh_session, remote_virsh_session)

    migration_options = params.get('migration_options', '')
    vm_name = params.get("migrate_main_vm")
    migrate_again = "yes" == params.get("migrate_again", "no")

    virsh_session = None
    remote_virsh_session = None

    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)
    params.update({"migration_obj": migration_obj})

    setup_test = eval("setup_%s" % migration_options) if "setup_%s" % migration_options in \
        locals() else migration_obj.setup_connection

    try:
        base_steps.setup_network_data_transport(params)
        setup_test()
        # Monitor event on source/target host
        virsh_session, remote_virsh_session = migration_base.monitor_event(params)
        migration_obj.run_migration()
        if migrate_again:
            migration_obj.run_migration_again()
        verify_test()
    finally:
        migration_obj.cleanup_connection()
