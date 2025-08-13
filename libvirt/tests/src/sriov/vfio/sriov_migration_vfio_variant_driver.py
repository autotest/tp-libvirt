import time
from provider.migration import base_steps
from provider.sriov import sriov_vfio

from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_virtio
from virttest.utils_libvirt import libvirt_vmxml


def run(test, params, env):
    """
    Migration testing of VM with vfio variant driver
    """
    managed = params.get("managed")
    iommu_dict = eval(params.get("iommu_dict", "{}"))

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    dev_names = []

    migration_obj = base_steps.MigrationBase(test, vm, params)

    new_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm.name)
    orig_vm_xml = new_xml.copy()
    try:
        libvirt_vmxml.remove_vm_devices_by_type(vm, 'interface')
        libvirt_vmxml.remove_vm_devices_by_type(vm, 'hostdev')
        if iommu_dict:
            libvirt_virtio.add_iommu_dev(vm, iommu_dict)

        dev_names = sriov_vfio.attach_dev(vm, params)
        if not vm.is_alive():
            vm.start()
        vm.cleanup_serial_console()
        vm.create_serial_console()
        vm.wait_for_serial_login().close()
        time.sleep(30)

        test.log.debug(f'VMXML of {vm_name}:\n{virsh.dumpxml(vm_name).stdout_text}')

        migration_obj.setup_connection()
        test.log.info("TEST_STEP: Migrate the VM to the target host.")
        migration_obj.run_migration()
        migration_obj.verify_default()

        migrate_vm_back = "yes" == params.get("migrate_vm_back", "yes")
        if migrate_vm_back:
            test.log.info("TEST_STEP: Migrate back the VM to the source host.")
            migration_obj.run_migration_back()
            migration_obj.migration_test.ping_vm(vm, params)
            migration_obj.check_vm_cont_ping(False)

    finally:
        orig_vm_xml.sync()
        if managed == "no":
            for dev in dev_names:
                virsh.nodedev_reattach(dev, debug=True)
