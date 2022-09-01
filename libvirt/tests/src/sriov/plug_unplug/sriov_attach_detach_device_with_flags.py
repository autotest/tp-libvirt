from provider.sriov import check_points
from provider.sriov import sriov_base

from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vfio
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Attach/detach-device a hostdev interface or device with various flags
    """
    def get_vm_hostdev(device_type):
        """
        Get VM hostdev device/interface

        :param device_type: Device type
        :return: The first VM hostdev device or interface
        """
        try:
            vm_hostdev = vm_xml.VMXML.new_from_dumpxml(vm.name)\
                .devices.by_device_tag(device_type)[0]
        except IndexError:
            vm_hostdev = vm_xml.VMXML.new_from_inactive_dumpxml(vm.name)\
                .devices.by_device_tag(device_type)[0]
        return vm_hostdev

    def check_hostdev_xml(vmxml, device_type, status_error=False):
        """
        Check VM hostdev xml info

        :param vmxml: The vmxml object
        :param device_type: Device type
        :param status_error: Whether a hostdev interface/device should exist,
            defaults to False
        """
        vm_hostdevs = vmxml.devices.by_device_tag(device_type)
        if status_error != (len(vm_hostdevs) == 1):
            test.fail("Got incorrect hostdev device/interface number: %d!"
                      % len(vm_hostdevs))
        if status_error:
            vm_hostdev_dict = vm_hostdevs[0].fetch_attrs()
            test.log.debug("hostdev device/interface: %s", vm_hostdev_dict)
            if vm_hostdev_dict.get('driver'):
                if vm_hostdev_dict['driver']['driver_attr']['name'] != 'vfio':
                    test.fail("The driver name should be 'vfio'!")

    def check_vm_xml(vm, device_type, params, status_error=False):
        """
        Check VM xml info

        :param vm: VM object
        :param device_type: Device type
        :param params: Dictionary with the test parameters
        :param status_error: Whether a hostdev interface/device should exist,
            defaults to False
        """
        if params.get('expr_active_xml_changes', 'no') == "yes":
            test.log.debug("checking active vm xml after attaching hostdev.")
            check_hostdev_xml(
                vm_xml.VMXML.new_from_dumpxml(vm.name), device_type, status_error)
        if params.get('expr_inactive_xml_changes', 'no') == "yes":
            test.log.debug("checking inactive vm xml after attaching hostdev.")
            check_hostdev_xml(
                vm_xml.VMXML.new_from_inactive_dumpxml(vm.name), device_type,
                status_error)

    def run_test():
        """
        Attach/detach-device a hostdev interface or device with
        various flags(--live/config/persistent/current) for vm states(shutoff,
        running).
        """
        if start_vm and not vm.is_alive():
            vm.start()
            vm_session = vm.wait_for_login()

        libvirt_vfio.check_vfio_pci(sriov_test_obj.vf_pci, True)

        test.log.info("TEST_STEP1: Attach a hostdev interface/device to VM")
        iface_dev = sriov_test_obj.create_iface_dev(dev_type, iface_dict)
        result = virsh.attach_device(vm.name, iface_dev.xml, flagstr=flagstr,
                                     debug=True)
        libvirt.check_exit_status(result, status_error)
        if status_error:
            return

        test.log.info("TEST_STEP2: Check VM xml.")
        check_vm_xml(vm, device_type, params, True)
        if 'vm_session' in locals():
            check_points.check_vm_network_accessed(vm_session)

        test.log.info("TEST_STEP3: Detach the hostdev interface/device")
        vm_hostdev = get_vm_hostdev(device_type)
        wait_for_event = True if vm.is_alive() and not flagstr.count('config') \
            else False
        virsh.detach_device(vm.name, vm_hostdev.xml, debug=True,
                            flagstr=flagstr, ignore_errors=False,
                            wait_for_event=wait_for_event, event_timeout=15)

        test.log.info("TEST_STEP4: Check VM xml.")
        check_vm_xml(vm, device_type, params)

    dev_type = params.get("dev_type", "")
    flagstr = params.get('flagstr')
    if flagstr:
        flagstr = "--%s" % flagstr
    start_vm = "yes" == params.get("start_vm")
    status_error = "yes" == params.get("status_error")

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    sriov_test_obj = sriov_base.SRIOVTest(vm, test, params)

    iface_dict = sriov_test_obj.parse_iface_dict()
    device_type = "hostdev" if dev_type == "hostdev_device" else "interface"
    try:
        sriov_test_obj.setup_default()
        run_test()
    finally:
        sriov_test_obj.teardown_default()
