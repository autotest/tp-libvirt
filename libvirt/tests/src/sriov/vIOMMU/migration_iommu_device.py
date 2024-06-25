from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml

from provider.migration import base_steps
from provider.viommu import viommu_base


def run(test, params, env):
    """
    Test vm migration with iommu device
    This case starts vm with different iommu device settings then migrate it
    to and back to check network works well.
    """
    def check_iommu_xml(vm, params):
        """
        Check the iommu xml of the migrated vm

        :param vm: VM object
        :param params: Dictionary with the test parameters
        """
        iommu_dict = eval(params.get('iommu_dict', '{}'))
        if not iommu_dict:
            return
        server_ip = params.get("server_ip")
        server_user = params.get("server_user", "root")
        server_pwd = params.get("server_pwd")
        remote_virsh_dargs = {'remote_ip': server_ip,
                              'remote_user': server_user,
                              'remote_pwd': server_pwd,
                              'unprivileged_user': None,
                              'ssh_remote_auth': True}
        virsh_session_remote = virsh.VirshPersistent(**remote_virsh_dargs)
        migrated_vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(
                vm.name, virsh_instance=virsh_session_remote)
        vm_iommu = migrated_vmxml.devices.by_device_tag(
            'iommu')[0].fetch_attrs()
        for attr, value in iommu_dict.items():
            if vm_iommu.get(attr) != value:
                test.fail('iommu xml(%s) comparison failed.'
                          'Expected "%s",got "%s".'
                          % (attr, value, vm_iommu))

    def setup_test():
        """
        Setup test
        """
        test.log.info("TEST_SETUP: Prepare a VM with different iommu devices.")
        test_obj.setup_iommu_test(iommu_dict=iommu_dict,
                                  cleanup_ifaces=cleanup_ifaces)
        test_obj.prepare_controller()
        test.log.debug(vm_xml.VMXML.new_from_dumpxml(vm.name))
        for dev in ["disk", "video"]:
            dev_dict = eval(params.get('%s_dict' % dev, '{}'))
            if dev == "disk":
                dev_dict = test_obj.update_disk_addr(dev_dict)
            test.log.debug(dev_dict)
            libvirt_vmxml.modify_vm_device(
                vm_xml.VMXML.new_from_dumpxml(vm.name), dev, dev_dict)
        if cleanup_ifaces:
            libvirt_vmxml.modify_vm_device(
                    vm_xml.VMXML.new_from_dumpxml(vm.name),
                    "interface", iface_dict)
        migration_obj.setup_default()

    cleanup_ifaces = "yes" == params.get("cleanup_ifaces", "yes")
    iommu_dict = eval(params.get('iommu_dict', '{}'))
    iface_dict = eval(params.get('iface_dict', '{}'))

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    test_obj = viommu_base.VIOMMUTest(vm, test, params)
    migration_obj = base_steps.MigrationBase(test, vm, params)

    try:
        setup_test()
        test.log.info("TEST_STEP: Migrate the VM to the target host.")
        migration_obj.run_migration()
        migration_obj.verify_default()
        check_iommu_xml(vm, params)

        test.log.info("TEST_STEP: Migrate back the VM to the source host.")
        migration_obj.run_migration_back()
        migration_obj.migration_test.ping_vm(vm, params)
    finally:
        test_obj.teardown_iommu_test()
