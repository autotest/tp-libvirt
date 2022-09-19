from virttest import utils_misc
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_network
from virttest.utils_libvirt import libvirt_vfio
from virttest.utils_test import libvirt

from provider.sriov import sriov_base
from provider.sriov import check_points


def run(test, params, env):
    """
    Start vm with a hostdev device or hostdev interface
    """
    def run_test():
        """
        Start vm with an interface/device of hostdev type, and test the network
        function.
        """

        test.log.info("TEST_STEP1: Attach a hostdev interface/device to VM")
        iface_dev = sriov_test_obj.create_iface_dev(dev_type, iface_dict)
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        libvirt.add_vm_device(vmxml, iface_dev)

        test.log.info("TEST_STEP2: Start the VM")
        vm.start()
        vm_session = vm.wait_for_serial_login(timeout=240)

        test.log.info("TEST_STEP3: Check hostdev xml after VM startup")
        if dev_type == "network_interface":
            iface_dict.update({'type_name': 'hostdev'})
        check_points.comp_hostdev_xml(vm, device_type, iface_dict)

        test.log.info("TEST_STEP4: Check hostdev information - mac, "
                      "vlan, network accessibility and so on")
        check_points.check_mac_addr(
            vm_session, vm.name, device_type, iface_dict)

        if 'vlan' in iface_dict:
            check_points.check_vlan(sriov_test_obj.pf_name, iface_dict)
        else:
            check_points.check_vm_network_accessed(vm_session)

        if network_dict:
            libvirt_network.check_network_connection(
                network_dict.get("name"), 1)

        test.log.info("TEST_STEP5: Destroy VM")
        vm.destroy(gracefully=False)

        test.log.info("TEST_STEP6: Check environment recovery - mac, driver, "
                      "vlan")
        if network_dict:
            libvirt_network.check_network_connection(
                network_dict.get("name"), 0)

        check_points.check_vlan(sriov_test_obj.pf_name, iface_dict, True)

        if not utils_misc.wait_for(
            lambda: libvirt_vfio.check_vfio_pci(
                dev_pci, not managed_disabled, True), 10, 5):
            test.fail("Got incorrect driver!")
        if managed_disabled:
            virsh.nodedev_reattach(dev_name, debug=True, ignore_status=False)
            libvirt_vfio.check_vfio_pci(dev_pci, True)
        check_points.check_mac_addr_recovery(
            sriov_test_obj.pf_name, device_type, iface_dict)

    dev_type = params.get("dev_type", "")
    dev_source = params.get("dev_source", "")

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    sriov_test_obj = sriov_base.SRIOVTest(vm, test, params)
    if dev_type == "hostdev_device" and dev_source.startswith("pf"):
        dev_name = sriov_test_obj.pf_dev_name
        dev_pci = sriov_test_obj.pf_pci
    else:
        dev_name = sriov_test_obj.vf_dev_name
        dev_pci = sriov_test_obj.vf_pci

    iface_dict = sriov_test_obj.parse_iface_dict()
    network_dict = sriov_test_obj.parse_network_dict()
    managed_disabled = iface_dict.get('managed') != "yes" or \
        (network_dict and network_dict.get('managed') != "yes")
    device_type = "hostdev" if dev_type == "hostdev_device" else "interface"

    try:
        sriov_test_obj.setup_default(dev_name=dev_name,
                                     managed_disabled=managed_disabled,
                                     network_dict=network_dict)
        run_test()

    finally:
        sriov_test_obj.teardown_default(
            managed_disabled=managed_disabled,
            dev_name=dev_name, network_dict=network_dict)
