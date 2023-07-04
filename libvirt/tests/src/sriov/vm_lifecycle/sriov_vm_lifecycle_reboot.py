import re

from provider.sriov import check_points
from provider.sriov import sriov_base

from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


def get_avg_ping_time(vm_session, params):
    """
    Get the average time of ping command

    :param vm_session: VM session
    :param params: Test parameters
    :return: The average response time
    """
    ping_count = int(params.get("ping_count", '10'))
    ping_time = check_points.check_vm_network_accessed(
        vm_session, ping_count=ping_count, ping_timeout=30)
    return int(re.findall(
        "10 packets.*time (\d+)", ping_time)[0]) / ping_count


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
        if params.get("iface_dict2"):
            vf_pci_addr2 = sriov_test_obj.vf_pci_addr2
            iface_dict2 = eval(params.get("iface_dict2"))
            iface_dev2 = sriov_test_obj.create_iface_dev(dev_type, iface_dict2)
            libvirt.add_vm_device(
                vm_xml.VMXML.new_from_dumpxml(vm_name), iface_dev2)

        test.log.info("TEST_STEP2: Start the VM")
        vm.start()
        vm.cleanup_serial_console()
        vm.create_serial_console()
        vm_session = vm.wait_for_serial_login(timeout=240)
        if params.get("check_ping_time") == "yes":
            avg_ping_time = get_avg_ping_time(vm_session, params)

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
            if params.get("check_ping_time") == "yes":
                avg_ping_time2 = get_avg_ping_time(vm_session, params)
                test.log.debug(f"ping before reboot: {avg_ping_time},"
                               f"ping after reboot: {avg_ping_time2}")
                if avg_ping_time >= avg_ping_time * 3:
                    test.fail("High latency after soft reboot!")
            else:
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

    try:
        test_setup(**test_dict)
        run_test()

    finally:
        test_teardown(**test_dict)
