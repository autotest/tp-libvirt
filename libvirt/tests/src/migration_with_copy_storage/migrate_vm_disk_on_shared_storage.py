# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Liping Cheng <lcheng@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

from virttest.libvirt_xml import vm_xml

from provider.migration import base_steps


def run(test, params, env):
    """
    To verify that live migration with copying storage will fail when vm disk
    is located on shared storage which can be accessed on both src and target
    host.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    def setup_test():
        """
        Setup steps

        """
        test.log.info("Setup steps.")
        migration_obj.setup_connection()
        new_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        domain_disk = new_xml.get_devices(device_type="disk")[0]
        del domain_disk['share']
        del domain_disk['readonly']
        new_xml.remove_all_disk()
        new_xml.add_device(domain_disk)
        new_xml.sync()
        vm.start()
        vm.wait_for_login().close()

    vm_name = params.get("migrate_main_vm")
    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)

    try:
        setup_test()
        migration_obj.run_migration()
        migration_obj.verify_default()
    finally:
        migration_obj.cleanup_connection()
