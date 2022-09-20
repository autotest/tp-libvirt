import time

from virttest import utils_libvirtd
from virttest import utils_misc
from virttest import utils_net
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vfio
from virttest.utils_libvirt import libvirt_vmxml

from provider.sriov import check_points
from provider.sriov import sriov_base


def run(test, params, env):
    """
    Attach-device of hostdev type to guest for special situations.
    """
    def check_vm_iface(vm_session):
        """
        Check the VM's interface

        :param vm_session: The session to the VM
        """
        # FIXME: If the "IP" command is executed immediately after the interface
        # is attached, it will be hung.
        time.sleep(10)
        p_iface, _v_ifc = utils_net.get_remote_host_net_ifs(vm_session)

        if p_iface == (test_scenario == "unassigned_address"):
            test.fail("Got incorrect interface: %s!" % p_iface)

        if test_scenario == "readonly_mode":
            check_points.check_vm_network_accessed(vm_session)

    def setup_test():
        """
        Setup test
        """
        if test_scenario == "boot_order":
            test.log.info("TEST_SETUP: Update disk's boot order.")
            vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
            vmxml.remove_all_boots()
            test.log.debug(vmxml)
            libvirt_vmxml.modify_vm_device(vmxml, 'disk', disk_dict)

            test.log.info("TEST_SETUP: Add 'rom' to interface's dict.")
            rom_file = sriov_test_obj.get_rom_file()
            iface_dict.update({'rom': {'bar': 'on', 'file': rom_file}})

        elif test_scenario == "readonly_mode":
            test.log.info("TEST_SETUP: Re-mounting sysfs with ro mode.")
            utils_misc.mount('/sys', '', None, 'remount,ro')
            utils_libvirtd.Libvirtd('virtqemud').restart()

    def run_test():
        """
        Attach-device of hostdev type to guest for special situations.
        """
        test.log.info("TEST_STEP1: Start the VM")
        vm.start()
        vm_session = vm.wait_for_serial_login(timeout=240)

        test.log.info("TEST_STEP2: Attach a hostdev interface/device to VM")
        iface_dev = sriov_test_obj.create_iface_dev(dev_type, iface_dict)
        virsh.attach_device(vm.name, iface_dev.xml, debug=True,
                            ignore_status=False)

        test.log.info("TEST_STEP3: Check hostdev xml/driver and VM interface.")
        libvirt_vfio.check_vfio_pci(dev_pci)
        check_points.comp_hostdev_xml(vm, device_type, iface_dict)
        check_vm_iface(vm_session)

        test.log.info("TEST_STEP4: Detach the hostdev interface/device.")
        vm_hostdev = vm_xml.VMXML.new_from_dumpxml(vm.name)\
            .devices.by_device_tag(device_type)[0]
        virsh.detach_device(vm.name, vm_hostdev.xml, debug=True,
                            ignore_status=False, wait_for_event=True)
        cur_hostdevs = vm_xml.VMXML.new_from_dumpxml(vm.name)\
            .devices.by_device_tag(device_type)
        if cur_hostdevs:
            test.fail("Got hostdev interface/device(%s) after detaching the "
                      "device!" % cur_hostdevs)

        if not utils_misc.wait_for(
            lambda: libvirt_vfio.check_vfio_pci(
                dev_pci, not managed_disabled, True), 10, 5):
            test.fail("Got incorrect driver!")

        if test_scenario == "readonly_mode":
            utils_misc.mount('/sys', '', None, 'remount,rw')
        if managed_disabled:
            virsh.nodedev_reattach(dev_name, debug=True, ignore_status=False)
            libvirt_vfio.check_vfio_pci(dev_pci, True)

    dev_type = params.get("dev_type", "")
    test_scenario = params.get("test_scenario", "")

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    sriov_test_obj = sriov_base.SRIOVTest(vm, test, params)

    dev_name = sriov_test_obj.vf_dev_name
    dev_pci = sriov_test_obj.vf_pci

    disk_dict = eval(params.get("disk_dict", '{}'))
    iface_dict = sriov_test_obj.parse_iface_dict()
    managed_disabled = iface_dict.get('managed') != "yes"

    device_type = "hostdev" if dev_type == "hostdev_device" else "interface"
    try:
        sriov_test_obj.setup_default(dev_name=dev_name,
                                     managed_disabled=managed_disabled)
        setup_test()
        run_test()

    finally:
        if test_scenario == "readonly_mode":
            utils_misc.mount('/sys', '', None, 'remount,rw')
        sriov_test_obj.teardown_default(
                    managed_disabled=managed_disabled,
                    dev_name=dev_name)
