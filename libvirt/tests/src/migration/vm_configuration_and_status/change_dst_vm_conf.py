# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0
#
#   Author: Liping Cheng <lcheng@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

from virttest import virsh
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml

from provider.migration import base_steps


def run(test, params, env):
    """
    To test target vm name, live xml and persistent xml can be changed via virsh
    migrate options during migration.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    def setup_test():
        """
        Setup steps
        """
        persistent_option = params.get("persistent_option")
        dname = params.get("dname")
        xml_title = params.get("xml_title")
        xml_path = params.get("xml_path")
        persistent_xml_path = params.get("persistent_xml_path")
        extra = params.get("virsh_migrate_extra")

        test.log.info("Setup test.")
        migration_obj.setup_connection()
        if persistent_option:
            extra = "%s %s" % (extra, persistent_option)
        if dname:
            extra = "%s --dname %s" % (extra, dname)
        if xml_path:
            if xml_path == "xml_exist":
                mig_xml = vm_xml.VMXML.new_from_dumpxml(vm_name, options="--migratable")
                graphic_xml = mig_xml.get_devices(device_type="graphics")[0]
                mig_xml.del_device(graphic_xml)
                graphic_xml["autoport"] = "no"
                graphic_xml["port"] = "6666"
                mig_xml.add_device(graphic_xml)
                mig_xml.sync()
                extra = "%s --xml %s" % (extra, mig_xml.xml)
            else:
                extra = "%s --xml /var/tmp/xml_non_exist_test" % extra
        if persistent_xml_path:
            if persistent_xml_path == "persistent_xml_exist":
                persistent_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
                persistent_xml.title = xml_title
                extra = "%s --persistent-xml %s" % (extra, persistent_xml.xml)
            else:
                extra = "%s --persistent-xml /var/tmp/persistent_xml_non_exist_test" % extra
        params.update({"virsh_migrate_extra": extra})
        vm.start()
        vm.wait_for_login().close()

    def verify_test():
        """
        Verify steps
        """
        desturi = params.get("virsh_migrate_desturi")
        persistent_option = params.get("persistent_option")
        dname = params.get("dname")
        xml_title = params.get("xml_title")
        xml_path = params.get("xml_path")
        persistent_xml_path = params.get("persistent_xml_path")
        src_vm_state = params.get("virsh_migrate_src_state")
        dest_vm_state = params.get("virsh_migrate_dest_state")
        status_error = "yes" == params.get("status_error", "no")

        test.log.info("Verify test.")
        if status_error:
            migration_obj.verify_default()
        else:
            if dname:
                migration_obj.migration_test.ping_vm(vm, params, uri=desturi)
                if not libvirt.check_vm_state(vm_name, src_vm_state, uri=migration_obj.src_uri, debug=True):
                    test.fail("When with '--dname', check source vm state failed.")
                if not libvirt.check_vm_state(dname, dest_vm_state, uri=desturi, debug=True):
                    test.fail("When with '--dname', check dest vm state failed.")
            else:
                migration_obj.verify_default()
            vm.name = dname if dname else vm_name
            new_xml = virsh.dumpxml(vm.name, debug=True, uri=desturi)
            if dname:
                if dname not in new_xml.stdout_text.strip():
                    test.fail(f"Not found {dname} in vm xml.")

            if xml_path:
                if xml_path == "xml_exist":
                    libvirt.check_result(new_xml, expected_match="autoport='no'")
                else:
                    libvirt.check_result(new_xml, expected_match="autoport='yes'")
            virsh.destroy(vm.name, debug=True, uri=desturi)
            if persistent_option:
                if not libvirt.check_vm_state(vm.name, "shut off", uri=desturi):
                    test.fail("When migration with '--persistent', check dest vm state failed.")
                if dname:
                    if dname not in virsh.dom_list("--all", debug=True, uri=desturi).stdout_text:
                        test.fail(f"When migration with '--persistent --dname', not found {dname} in vm list.")
                if persistent_xml_path:
                    remote_virsh = virsh.Virsh(uri=desturi)
                    vmxml_inactive = vm_xml.VMXML.new_from_inactive_dumpxml(vm.name, "--security-info", remote_virsh)
                    if vmxml_inactive.title != xml_title:
                        test.fail("'--persistent-xml' didn't take effect in live migration.")
            else:
                if virsh.domain_exists(vm.name, uri=desturi):
                    test.fail("The domain is found on the target host, but expected not.")

    vm_name = params.get("migrate_main_vm")
    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)

    try:
        setup_test()
        migration_obj.run_migration()
        verify_test()
    finally:
        migration_obj.cleanup_connection()
