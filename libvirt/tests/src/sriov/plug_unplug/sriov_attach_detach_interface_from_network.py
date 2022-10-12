from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vfio

from provider.sriov import check_points
from provider.sriov import sriov_base


def run(test, params, env):
    """
    Attach interface to guest from a hostdev network
    """
    def run_test():
        """
        Attach interface to guest from a hostdev network and detach it.
        """
        test.log.info("TEST_STEP1: Start the VM")
        vm.start()
        vm_session = vm.wait_for_serial_login(timeout=240)

        test.log.info("TEST_STEP2: Attach a hostdev interface to VM")
        opts = "network %s" % network_dict['name']
        virsh.attach_interface(vm.name, opts, debug=True,
                               ignore_status=False)

        check_points.check_vm_network_accessed(vm_session)

        test.log.info("TEST_STEP3: Detach the hostdev interface.")
        virsh.detach_interface(vm.name, "hostdev", debug=True,
                               ignore_status=False, wait_for_event=True)
        cur_hostdevs = vm_xml.VMXML.new_from_dumpxml(vm.name)\
            .devices.by_device_tag("interface")
        if cur_hostdevs:
            test.fail("Got hostdev interface(%s) after detaching the "
                      "device!" % cur_hostdevs)

        libvirt_vfio.check_vfio_pci(sriov_test_obj.vf_pci, True)

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    sriov_test_obj = sriov_base.SRIOVTest(vm, test, params)
    network_dict = sriov_test_obj.parse_network_dict()

    try:
        sriov_test_obj.setup_default(network_dict=network_dict)
        run_test()

    finally:
        sriov_test_obj.teardown_default(network_dict=network_dict)
