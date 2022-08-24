from provider.sriov import sriov_base
from provider.sriov import check_points

from virttest import utils_misc
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vfio


def run(test, params, env):
    """
    Attach/detach-device a hostdev interface or device to/from guest
    """
    def run_test():
        """
        Live hotplug/unplug an interface/device of hostdev type to/from guest,
        and test the network function.
        """
        test.log.info("TEST_STEP1: Start the VM")
        vm.start()
        vm_session = vm.wait_for_serial_login(timeout=240)

        test.log.info("TEST_STEP2: Attach a hostdev interface/device to VM")
        iface_dev = sriov_test_obj.create_iface_dev(dev_type, iface_dict)
        virsh.attach_device(vm.name, iface_dev.xml, debug=True,
                            ignore_errors=False)

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

        test.log.info("TEST_STEP5: Detach the hostdev interface/device.")
        vm_hostdev = vm_xml.VMXML.new_from_dumpxml(vm.name)\
            .devices.by_device_tag(device_type)[0]
        virsh.detach_device(vm.name, vm_hostdev.xml, debug=True,
                            ignore_errors=False)
        cur_hostdevs = vm_xml.VMXML.new_from_dumpxml(vm.name)\
            .devices.by_device_tag(device_type)
        if cur_hostdevs:
            test.fail("Got hostdev interface/device(%s) after detaching the "
                      "device!" % cur_hostdevs)

        if not utils_misc.wait_for(
            lambda: libvirt_vfio.check_vfio_pci(
                dev_pci, not managed_disabled, True), 10, 5):
            test.fail("Got incorrect driver!")
        if managed_disabled:
            virsh.nodedev_reattach(dev_name, debug=True, ignore_errors=False)
            libvirt_vfio.check_vfio_pci(dev_pci, True)

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
