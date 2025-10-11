# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Red hat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Liang Cong <lcong@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import os

from aexpect import remote
from avocado.utils import process

from virttest import libvirt_version
from virttest import test_setup
from virttest import utils_libvirtd
from virttest import utils_misc
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.memory import Memory
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
    def check_hugepage_support():
        """
        Verify hugepage size is supported by source and destination hosts
        """
        memory_base.check_mem_page_sizes(test, hp_size=hp_size)
        memory_base.check_mem_page_sizes(test, hp_size=hp_size, session=remote_session)

    def setup_hugepage():
        """
        Set hugepages on source and destination hosts
        """
        check_hugepage_support()
        src_hpc = test_setup.HugePageConfig(params)
        dst_hpc = test_setup.HugePageConfig(params, session=remote_session)
        global hpc_list
        hpc_list = [src_hpc, dst_hpc]
        for hpc_inst in hpc_list:
            hpc_inst.set_kernel_hugepages(hp_size, hp_num, False)
            hpc_inst.mount_hugepage_fs()
            utils_libvirtd.Libvirtd(session=hpc_inst.session).restart()

    def check_mem_device_xml(exp_xpath, alias_name, virsh_obj=virsh):
        """
        Check domain XML by xpaths

        :param exp_xpath: list of memory device xpaths to check
        :param alias_name: alias name of the memory device
        :param virsh_obj: libvirt connection object
        :raises TestFail: if expected memory XML not found on VM
        """
        def _check_mem():
            """
            Check memory devices xml of domain XML

            return True if all xpaths are found, False otherwise
            """
            guest_xml = vm_xml.VMXML.new_from_dumpxml(
                vm_name, virsh_instance=virsh_obj)
            memory_devices = guest_xml.devices.by_device_tag('memory')
            target_memory_device = None
            for memory_device in memory_devices:
                if alias_name == memory_device.alias.get('name'):
                    target_memory_device = memory_device
            if not target_memory_device:
                test.fail(f"Memory device with alias {alias_name} not found in domain xml {guest_xml}")
            return libvirt_vmxml.check_guest_xml_by_xpaths(target_memory_device, exp_xpath, True, True)

        if not utils_misc.wait_for(lambda: _check_mem(), timeout=30):
            guest_xml = vm_xml.VMXML.new_from_dumpxml(vm_name)
            test.fail(f"Expected xml xpath:{exp_xpath} not found in domain xml:{guest_xml}")

    def check_domain_xml(virsh_obj=virsh):
        """
        Check domain xml

        :param virsh_obj: virsh instance
        """
        check_mem_device_xml(mem_define_xpath, mem_define_alias, virsh_obj=virsh_obj)
        check_mem_device_xml(mem_hotplug_xpath, mem_hotplug_alias, virsh_obj=virsh_obj)
        guest_xml = vm_xml.VMXML.new_from_dumpxml(vm_name, virsh_instance=virsh_obj)
        libvirt_vmxml.check_guest_xml_by_xpaths(guest_xml, mem_xpath, False, False)

    def check_nvdimm_file_content(session, device, mount_point, file_path, exp_content):
        """
        Check the content of a file on a NVDIMM device

        :param session: vm session
        :param device: NVDIMM device
        :param mount_point: mount point of the NVDIMM device
        :param file_path: path of the file on the NVDIMM device
        :param exp_content: expected content of the file
        :raises TestFail: if expected content in file not found in guest os
        """
        session.cmd_output(f"mount -o dax {device} {mount_point}")
        file_content = session.cmd_output(f"cat {file_path}").strip()
        if file_content != exp_content:
            test.fail(f"Expected {file_path} content: {exp_content}, but found {file_content}")

    def setup_nvdimm():
        """
        Create nvdimm file
        """
        nvdimm_paths_cmds = eval(params.get("nvdimm_paths_cmds"))
        for cmd in nvdimm_paths_cmds:
            process.run(cmd, ignore_status=False)

    libvirt_version.is_libvirt_feature_supported(params)
    mem_device_model = params.get("mem_device_model")
    vm_name = params.get("migrate_main_vm")
    vm = env.get_vm(vm_name)
    server_ip = params.get("server_ip")
    server_pwd = params.get("server_pwd")
    migration_obj = base_steps.MigrationBase(test, vm, params)
    remote_session = remote.remote_login(
        client="ssh",
        host=server_ip,
        port=22,
        username="root",
        password=server_pwd,
        prompt=r"[$#%]",
    )

    vm_attrs = eval(params.get("vm_attrs"))
    desturi = params.get("virsh_migrate_desturi")
    hp_size = int(params.get("hp_size"))
    hp_num = params.get("hp_num")
    mem_define = eval(params.get("mem_define"))
    mem_hotplug = eval(params.get("mem_hotplug"))
    nvdimm_define_device = params.get("nvdimm_define_device")
    nvdimm_hotplug_device = params.get("nvdimm_hotplug_device")
    nvdimm_define_mount_path = params.get("nvdimm_define_mount_path")
    nvdimm_hotplug_mount_path = params.get("nvdimm_hotplug_mount_path")
    nvdimm_define_file = params.get("nvdimm_define_file")
    nvdimm_hotplug_file = params.get("nvdimm_hotplug_file")
    nvdimm_define_content = params.get("nvdimm_define_content")
    nvdimm_hotplug_content = params.get("nvdimm_hotplug_content")
    mem_define_alias = params.get("mem_define_alias")
    mem_hotplug_alias = params.get("mem_hotplug_alias")
    mem_define_xpath = eval(params.get("mem_define_xpath"))
    mem_hotplug_xpath = eval(params.get("mem_hotplug_xpath"))
    mem_xpath = eval(params.get("mem_xpath"))
    hpc_list = []

    try:
        setup_hugepage()
        if "nvdimm" == mem_device_model:
            setup_nvdimm()

        # Test steps
        # 1. Define the guest
        # 2. Start the guest
        # 3. Hotplug the memory device
        # 4. Check the guest config xml by virsh dump
        # 4.1. Create file on two nvdimm devices
        # 5. Migrate the guest
        # 6. Check the guest config xml on target server
        # 6.1. Check files on two nvdimm devices
        test.log.info("TEST_STEP1: Define the guest")
        memory_base.define_guest_with_memory_device(params, [mem_define], vm_attrs)
        migration_obj.setup_connection()

        test.log.info("TEST_STEP2: Start the guest")
        vm.start()
        vm.wait_for_login().close()

        test.log.info("TEST_STEP3: Hotplug the memory device")
        mem_device = Memory()
        mem_device.setup_attrs(**mem_hotplug)
        virsh.attach_device(vm_name, mem_device.xml, ignore_status=False)

        test.log.info("TEST_STEP4: Check the guest config xml by virsh dump")
        check_domain_xml(virsh_obj=virsh)

        if "nvdimm" == mem_device_model:
            test.log.info("TEST_STEP4.1: Create file on two nvdimm devices")
            with vm.wait_for_login() as vm_session:
                memory_base.create_file_within_nvdimm_disk(
                    test, vm_session, nvdimm_define_device,
                    nvdimm_define_file, nvdimm_define_mount_path,
                    test_str=nvdimm_define_content)
                memory_base.create_file_within_nvdimm_disk(
                    test, vm_session, nvdimm_hotplug_device,
                    nvdimm_hotplug_file, nvdimm_hotplug_mount_path,
                    test_str=nvdimm_hotplug_content)

        test.log.info("TEST_STEP5: Migrate the guest")
        migration_obj.run_migration()
        migration_obj.verify_default()

        test.log.info("TEST_STEP6: Check the guest config xml on target server")
        virsh_obj = virsh.VirshPersistent(uri=desturi)
        check_domain_xml(virsh_obj=virsh_obj)

        if "nvdimm" == mem_device_model:
            test.log.info("TEST_STEP6.1: Check files on two nvdimm devices")
            backup_uri, vm.connect_uri = vm.connect_uri, desturi
            vm.cleanup_serial_console()
            vm.create_serial_console()
            remote_vm_session = vm.wait_for_serial_login()
            check_nvdimm_file_content(
                remote_vm_session, nvdimm_define_device,
                nvdimm_define_mount_path, nvdimm_define_file,
                nvdimm_define_content)
            check_nvdimm_file_content(
                remote_vm_session, nvdimm_hotplug_device,
                nvdimm_hotplug_mount_path, nvdimm_hotplug_file,
                nvdimm_hotplug_content)
            remote_vm_session.close()
            vm.connect_uri = backup_uri

    finally:
        for hpc_inst in hpc_list:
            hpc_inst.cleanup()
            utils_libvirtd.Libvirtd(session=hpc_inst.session).restart()
        if "nvdimm" == mem_device_model:
            nvdimm_paths = eval(params.get("nvdimm_paths"))
            for n_path in nvdimm_paths:
                if os.path.exists(n_path):
                    os.remove(n_path)
        migration_obj.cleanup_connection()
        remote_session.close()
