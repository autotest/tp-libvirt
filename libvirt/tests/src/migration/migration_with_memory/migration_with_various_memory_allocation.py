# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Liang Cong <lcong@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
from virttest import libvirt_version
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml

from provider.migration import base_steps


def run(test, params, env):
    """
    Verify guest migration behaviors with various memory allocations.

    :param test: test object
    :param params: Dictionary of test parameters
    :param env: Dictionary of test environment
    """
    libvirt_version.is_libvirt_feature_supported(params)
    vm_name = params.get("migrate_main_vm")
    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)
    vmxml = migration_obj.orig_config_xml.copy()

    vm_attrs = eval(params.get("vm_attrs"))
    expect_xpath = eval(params.get("expect_xpath"))
    desturi = params.get("virsh_migrate_desturi")

    try:
        # Test steps
        # 1. Define and start the guest
        # 2. Migrate the guest
        # 3. Check the guest config xml on target server
        test.log.info("TEST_STEP1: Define and start the guest")
        vmxml.setup_attrs(**vm_attrs)
        vmxml.sync()
        migration_obj.setup_connection()
        vm.start()
        vm.wait_for_login().close()

        test.log.info("TEST_STEP2: Migrate the guest")
        migration_obj.run_migration()
        migration_obj.verify_default()

        test.log.info("TEST_STEP3: Check the guest config xml on target server")
        virsh_obj = virsh.VirshPersistent(uri=desturi)
        guest_xml = vm_xml.VMXML.new_from_dumpxml(vm_name, virsh_instance=virsh_obj)
        libvirt_vmxml.check_guest_xml_by_xpaths(guest_xml, expect_xpath)

    finally:
        migration_obj.cleanup_connection()
