import os

from virttest import utils_misc
from virttest import utils_net
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml

from provider.save import save_base
from provider.sriov import sriov_base
from provider.sriov import check_points as sriov_check_points
from provider.viommu import viommu_base
from provider.vm_lifecycle import lifecycle_base

VIRSH_ARGS = {'debug': True, 'ignore_status': False}


def check_network_access(test, vm, session, need_sriov, ping_dest):
    """
    Check network access in VM
    """
    if need_sriov:
        sriov_check_points.check_vm_network_accessed(session, ping_dest=ping_dest)
    else:
        s, o = utils_net.ping(ping_dest, count=5, timeout=10, session=session)
        if s:
            test.fail(f"Failed to ping {ping_dest}! status: {s}, output: {o}")


def setup_vm_xml(test, vm, test_obj, need_sriov=False, cleanup_ifaces=True):
    """
    Setup VM XML with required configurations
    """
    test.log.info("TEST_SETUP: Update VM XML.")
    # Setup IOMMU test environment
    test_obj.setup_iommu_test(iommu_dict=eval(test_obj.params.get('iommu_dict', '{}')),
                              cleanup_ifaces=cleanup_ifaces)
    test_obj.prepare_controller()

    # Configure devices
    for dev in ["disk", "video"]:
        dev_dict = eval(test_obj.params.get('%s_dict' % dev, '{}'))
        if dev == "disk":
            dev_dict = test_obj.update_disk_addr(dev_dict)
        libvirt_vmxml.modify_vm_device(
            vm_xml.VMXML.new_from_dumpxml(vm.name), dev, dev_dict)

    # Configure network interfaces
    if need_sriov:
        sriov_test_obj = sriov_base.SRIOVTest(vm, test, test_obj.params)
        iface_dicts = sriov_test_obj.parse_iface_dict()
        test.log.debug(iface_dicts)
        test_obj.params["iface_dict"] = str(sriov_test_obj.parse_iface_dict())

    iface_dict = test_obj.parse_iface_dict()
    if cleanup_ifaces:
        libvirt_vmxml.modify_vm_device(
                vm_xml.VMXML.new_from_dumpxml(vm.name),
                "interface", iface_dict)


def run(test, params, env):
    """
    Test lifecycle for vm with vIOMMU enabled with different devices.
    """
    cleanup_ifaces = "yes" == params.get("cleanup_ifaces", "yes")
    ping_dest = params.get('ping_dest', "127.0.0.1")
    test_scenario = params.get("test_scenario")
    need_sriov = "yes" == params.get("need_sriov", "no")

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)

    test_obj = viommu_base.VIOMMUTest(vm, test, params)

    try:
        # Setup VM XML configuration
        setup_vm_xml(test, vm, test_obj, need_sriov, cleanup_ifaces)

        # Start the VM
        test.log.info("TEST_STEP: Start the VM.")
        vm.start()
        test.log.debug(vm_xml.VMXML.new_from_dumpxml(vm.name))

        # Create a network check callback function
        def network_check_callback(session):
            check_network_access(test, vm, session, need_sriov, ping_dest)

        # Execute the appropriate test scenario using the provider module
        lifecycle_base.test_lifecycle_operation(
            test, vm, params, test_scenario, network_check_callback)

    finally:
        test_obj.teardown_iommu_test()
