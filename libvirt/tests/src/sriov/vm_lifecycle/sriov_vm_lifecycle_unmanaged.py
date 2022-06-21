from provider.sriov import sriov_base

from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vfio
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Start vm with interface/hostdev with managed=no or ignored
    """
    def run_test():
        """
        Cold/hot plug an interface/hostdev with managed=no or ignored

        1) Cold/hot plug an interface/hostdev with managed=no or ignored
        2) Check driver of the device
        """

        test.log.info("TEST_STEP1: Attach a hostdev interface/device to VM")
        at_options = '' if vm.is_alive() else '--config'
        iface_dev = sriov_test_obj.create_iface_dev(dev_type, iface_dict)
        result = virsh.attach_device(vm_name, iface_dev.xml, flagstr=at_options,
                                     debug=True)
        if at_options:
            result = virsh.start(vm_name, debug=True)
        libvirt.check_exit_status(result, status_error)
        if error_msg:
            libvirt.check_result(result, error_msg)

        test.log.info("TEST_STEP2: Check if the VM is not using VF.")
        libvirt_vfio.check_vfio_pci(dev_pci, True)

    dev_type = params.get("dev_type", "")
    dev_source = params.get("dev_source", "")
    test_scenario = params.get("test_scenario", "")
    status_error = "yes" == params.get("status_error", "no")
    error_msg = params.get("error_msg")
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    sriov_test_obj = sriov_base.SRIOVTest(vm, test, params)
    iface_dict = sriov_test_obj.parse_iface_dict()
    network_dict = sriov_test_obj.parse_network_dict()
    if dev_type == "hostdev_device" and dev_source.startswith("pf"):
        dev_pci = sriov_test_obj.pf_pci
    else:
        dev_pci = sriov_test_obj.vf_pci
    test_dict = {"network_dict": network_dict}

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    orig_config_xml = vmxml.copy()

    try:
        sriov_test_obj.setup_default(**test_dict)
        run_test()

    finally:
        sriov_test_obj.teardown_default(orig_config_xml, **test_dict)
