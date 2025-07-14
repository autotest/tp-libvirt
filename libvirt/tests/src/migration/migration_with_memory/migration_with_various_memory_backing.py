# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Liang Cong <lcong@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
from aexpect import remote

from virttest import libvirt_version
from virttest import test_setup
from virttest import utils_libvirtd
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml

from provider.memory import memory_base
from provider.migration import base_steps


def run(test, params, env):
    """
    Verify guest migration behaviors with various memory backing types.

    :param test: test object
    :param params: Dictionary of test parameters
    :param env: Dictionary of test environment
    """
    def check_memory_pages_support():
        """
        Verify memory page sizes support by source and destination hosts
        """
        default_page_size = int(params.get("default_page_size"))
        memory_base.check_mem_page_sizes(
            test, pg_size=default_page_size, hp_list=[int(hp_size)])
        memory_base.check_mem_page_sizes(test, pg_size=default_page_size, hp_list=[
                                         int(hp_size)], session=remote_session)

    libvirt_version.is_libvirt_feature_supported(params)
    cases = params.get("cases")
    vm_name = params.get("migrate_main_vm")
    vm = env.get_vm(vm_name)
    server_ip = params.get("server_ip")
    server_pwd = params.get("server_pwd")
    migration_obj = base_steps.MigrationBase(test, vm, params)
    vmxml = migration_obj.orig_config_xml.copy()
    remote_session = remote.remote_login(
        client="ssh",
        host=server_ip,
        port=22,
        username="root",
        password=server_pwd,
        prompt=r"[$#%]",
    )

    vm_attrs = eval(params.get("vm_attrs"))
    expect_xpath = eval(params.get("expect_xpath"))
    desturi = params.get("virsh_migrate_desturi")
    hp_size = params.get("hp_size")
    hp_num = params.get("hp_num")
    hpc_list = []

    try:
        if cases == "hugepage_with_numa":
            check_memory_pages_support()
            src_hpc = test_setup.HugePageConfig(params)
            dst_hpc = test_setup.HugePageConfig(params, session=remote_session)
            hpc_list = [src_hpc, dst_hpc]
            for hpc_inst in hpc_list:
                hpc_inst.set_kernel_hugepages(hp_size, hp_num, False)
                hpc_inst.mount_hugepage_fs()
                utils_libvirtd.Libvirtd(session=hpc_inst.session).restart()

        # Test steps
        # 1. Define the guest
        # 2. Start the guest
        # 3. Migrate the guest
        # 4. Check the guest config xml on target server
        test.log.info("TEST_STEP1: Define the guest")
        vmxml.setup_attrs(**vm_attrs)
        vmxml.sync()
        migration_obj.setup_connection()

        test.log.info("TEST_STEP2: Start the guest")
        vm.start()
        vm.wait_for_login().close()

        test.log.info("TEST_STEP3: Migrate the guest")
        migration_obj.run_migration()
        migration_obj.verify_default()

        test.log.info("TEST_STEP4: Check the guest config xml on target server")
        virsh_obj = virsh.VirshPersistent(uri=desturi)
        guest_xml = vm_xml.VMXML.new_from_dumpxml(vm_name, virsh_instance=virsh_obj)
        libvirt_vmxml.check_guest_xml_by_xpaths(guest_xml, expect_xpath)

    finally:
        migration_obj.cleanup_connection()
        for hpc_inst in hpc_list:
            hpc_inst.cleanup()
            utils_libvirtd.Libvirtd(session=hpc_inst.session).restart()
        remote_session.close()
