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
from virttest.libvirt_xml.devices import memballoon
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.migration import base_steps


def run(test, params, env):
    """
    Verify guest migration behaviors with various memory balloon models

    :param test: test objec
    :param params: Dictionary of test parameters
    :param env: Dictionary of test environment
    """
    libvirt_version.is_libvirt_feature_supported(params)
    vm_name = params.get("migrate_main_vm")
    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)
    vmxml = migration_obj.orig_config_xml.copy()

    mem_attrs = eval(params.get("mem_attrs"))
    expect_xpath = eval(params.get("expect_xpath"))
    desturi = params.get("virsh_migrate_desturi")
    dominfo_check = params.get("dominfo_check")
    memballoon_dict = eval(params.get("memballoon_dict"))

    try:
        # Test steps
        # 1. Define and start the guest
        # 2. Migrate the guest
        # 3. Check the guest config xml on target server
        # 4. Check memory info on target host by virsh dominfo
        test.log.info("TEST_STEP1: Define and start the guest")
        vmxml.del_device('memballoon', by_tag=True)
        mem_balloon = memballoon.Memballoon()
        mem_balloon.setup_attrs(**memballoon_dict)
        vmxml.devices = vmxml.devices.append(mem_balloon)

        vmxml.setup_attrs(**mem_attrs)
        vmxml.sync()
        migration_obj.setup_connection()
        vm.start()
        vm.wait_for_login().close()

        test.log.info("TEST_STEP2: Migrate the guest")
        migration_obj.run_migration()
        migration_obj.verify_default()

        test.log.info("TEST_STEP3: Check the guest config xml on target server")
        virsh_remote = virsh.VirshPersistent(uri=desturi)
        guest_xml = vm_xml.VMXML.new_from_dumpxml(vm_name, virsh_instance=virsh_remote)
        libvirt_vmxml.check_guest_xml_by_xpaths(guest_xml, expect_xpath)

        test.log.info("TEST_STEP4: Check memory info on target host by virsh dominfo")
        dominfo = virsh_remote.dominfo(vm_name, ignore_status=True, debug=True)
        libvirt.check_result(dominfo, expected_match=dominfo_check)

    finally:
        migration_obj.cleanup_connection()
