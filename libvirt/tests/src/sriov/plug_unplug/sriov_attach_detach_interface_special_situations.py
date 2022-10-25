from virttest import utils_net
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_kernel_module import KernelModuleHandler
from virttest.utils_libvirt import libvirt_vfio
from virttest.utils_test import libvirt

from provider.sriov import check_points
from provider.sriov import sriov_base


def run(test, params, env):
    """
    Attach/detach hostdev interface to/from guest for some special scenarios
    """
    def setup_test():
        """
        Setup test
        """
        if test_scenario == "to_vm_with_hostdev_ifaces":
            opts = "hostdev --source {0} {1} --config".format(
                sriov_test_obj.vf_pci2, attach_opt)

            test.log.info("TEST_SETUP: Attach-interface to the VM.")
            virsh.attach_interface(vm.name, opts,
                                   debug=True, ignore_status=False)

        elif test_scenario == "module_auto_reload":
            modules = ["vfio_pci", "vfio_iommu_type1"]
            test.log.info("TEST_SETUP: Remove modules: %s." % modules)
            for module_name in modules:
                KernelModuleHandler(module_name).unload_module()

    def check_vm_iface_after_detaching(test_scenario):
        """
        Check VM's iface number after detaching the device

        :param test_scenario: Test scenario
        """
        cur_hostdevs = vm_xml.VMXML.new_from_dumpxml(vm.name)\
            .devices.by_device_tag("interface")
        expr_iface_no = 1 if test_scenario == "to_vm_with_hostdev_ifaces" else 0
        if len(cur_hostdevs) != expr_iface_no:
            test.fail("Got hostdev interface(%s) after detaching the "
                      "device!" % cur_hostdevs)
        else:
            test.log.debug("Got correct iface number: %d." % expr_iface_no)

    def run_test():
        """
        Attach/detach-interface of hostdev type to/from guest for special
        situations.
        """
        test.log.info("TEST_STEP1: Start the VM")
        vm.start()
        vm_session = vm.wait_for_serial_login(timeout=240)

        mac_addr = utils_net.generate_mac_address_simple()
        opts = "hostdev --source {0} --mac {1} {2}".format(
            sriov_test_obj.vf_pci, mac_addr, attach_opt)

        test.log.info("TEST_STEP2: Attach-interface to the VM.")
        result = virsh.attach_interface(vm.name, opts, debug=True)
        libvirt.check_exit_status(result, status_error)
        if status_error:
            if err_msg:
                libvirt.check_result(result, err_msg)
            return

        test.log.info("TEST_STEP3: Check the vm's network connectivity and "
                      "whether the driver is vfio-pci.")
        check_points.check_vm_network_accessed(vm_session)
        libvirt_vfio.check_vfio_pci(sriov_test_obj.vf_pci)

        test.log.info("TEST_STEP4: Detach-interface from the VM.")
        virsh.detach_interface(vm.name, "hostdev --mac %s" % mac_addr,
                               debug=True, ignore_status=False,
                               wait_for_event=True)
        check_vm_iface_after_detaching(test_scenario)
        libvirt_vfio.check_vfio_pci(sriov_test_obj.vf_pci, True)

    attach_opt = params.get("attach_opt", "")
    err_msg = params.get("err_msg")
    status_error = "yes" == params.get("status_error", "no")
    test_scenario = params.get("test_scenario", "")

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    sriov_test_obj = sriov_base.SRIOVTest(vm, test, params)
    try:
        sriov_test_obj.setup_default()
        setup_test()
        run_test()

    finally:
        sriov_test_obj.teardown_default()
