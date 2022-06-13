from provider.sriov import check_points
from provider.sriov import sriov_base

from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Reboot vm with a hostdev interface/device
    """
    def run_test():
        """
        Reboot vm with a hostdev interface/device, make sure guest network
        connectivity still works well.

        1) Start the VM
        2) Suspend the VM and check the state
        3) Resume and check the state
        4) Check network connectivity
        """

        test.log.info("TEST_STEP1: Attach a hostdev interface/device to VM")
        iface_dev = sriov_test_obj.create_iface_dev(dev_type, iface_dict)
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        libvirt.add_vm_device(vmxml, iface_dev)

        test.log.info("TEST_STEP2: Start the VM")
        vm.start()
        vm.cleanup_serial_console()
        vm.create_serial_console()
        vm.wait_for_serial_login(timeout=240).close()

        test.log.info("TEST_STEP3: Reboot the vm")
        virsh.reboot(vm.name, debug=True, ignore_status=False)
        vm_session = vm.wait_for_serial_login(timeout=240)

        test.log.info("TEST_STEP4: Check network accessibility")
        if 'vlan' in iface_dict:
            check_points.check_vlan(sriov_test_obj.pf_name, iface_dict)
        else:
            br_name = None
            if test_scenario == 'failover':
                br_name = br_dict['source'].get('bridge')
            check_points.check_vm_network_accessed(vm_session,
                                                   tcpdump_iface=br_name,
                                                   tcpdump_status_error=True)

    dev_type = params.get("dev_type", "")
    dev_source = params.get("dev_source", "")
    test_scenario = params.get("test_scenario", "")

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    sriov_test_obj = sriov_base.SRIOVTest(vm, test, params)
    if dev_type == "hostdev_device" and dev_source.startswith("pf"):
        dev_name = sriov_test_obj.pf_dev_name
    else:
        dev_name = sriov_test_obj.vf_dev_name

    iface_dict = sriov_test_obj.parse_iface_dict()
    network_dict = sriov_test_obj.parse_network_dict()

    managed_disabled = iface_dict.get('managed') != "yes" or \
        (network_dict and network_dict.get('managed') != "yes")
    test_dict = sriov_test_obj.parse_iommu_test_params()
    test_dict.update({"network_dict": network_dict,
                      "managed_disabled": managed_disabled,
                      "dev_name": dev_name})
    br_dict = test_dict.get('br_dict', {'source': {'bridge': 'br0'}})

    test_setup = sriov_test_obj.setup_iommu_test if test_dict.get('iommu_dict') \
        else sriov_test_obj.setup_default
    test_teardown = sriov_test_obj.teardown_iommu_test if test_dict.get('iommu_dict')\
        else sriov_test_obj.teardown_default
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    orig_config_xml = vmxml.copy()

    try:
        test_setup(**test_dict)
        run_test()

    finally:
        test_teardown(orig_config_xml, **test_dict)
