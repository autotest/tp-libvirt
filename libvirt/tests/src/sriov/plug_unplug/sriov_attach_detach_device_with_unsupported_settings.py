from virttest import utils_net
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.sriov import sriov_base


def run(test, params, env):
    """
    Attach-device of hostdev type to guest with unsupported settings
    """
    def setup_test():
        """
        Setup test
        """
        sriov_test_obj.setup_default()
        if test_scenario == "inactive_pf":
            utils_net.Interface(sriov_test_obj.pf_name).down()
        elif test_scenario == "dup_alias":
            test.log.info("TEST_SETUP: Attach an interface.")
            vf_pci_addr = sriov_test_obj.vf_pci_addr
            pre_iface_dict = eval(params.get("pre_iface_dict", "{}"))
            libvirt_vmxml.modify_vm_device(
                vm_xml.VMXML.new_from_inactive_dumpxml(vm_name),
                'interface', pre_iface_dict)

    def run_test():
        """
        Attach-device of hostdev type to guest for special scenarios - negative
        """
        test.log.info("TEST_SETUP1: Start a vm.")
        vm.start()
        vm.wait_for_serial_login(timeout=240).close()

        test.log.info("TEST_STEP2: Attach a hostdev interface/device to VM")
        iface_dev = sriov_test_obj.create_iface_dev(dev_type, iface_dict)
        result = virsh.attach_device(vm_name, iface_dev.xml, debug=True)
        libvirt.check_result(result, err_msg)
        if test_scenario == "dup_alias":
            host_dev = vm_xml.VMXML.new_from_dumpxml(vm.name)\
                .devices.by_device_tag("interface")[0]
            test.log.info("TEST_STEP3: Detach the first hostdev interface.")
            virsh.detach_device(vm_name, host_dev.xml, wait_for_event=True,
                                debug=True, ignore_status=False)

            test.log.info("TEST_STEP4: Attach the second hostdev interface again.")
            virsh.attach_device(vm_name, iface_dev.xml, debug=True,
                                ignore_status=False)

    dev_type = params.get("dev_type", "")
    test_scenario = params.get("test_scenario")
    err_msg = params.get("err_msg")

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    sriov_test_obj = sriov_base.SRIOVTest(vm, test, params)
    iface_dict = sriov_test_obj.parse_iface_dict()
    try:
        setup_test()
        run_test()
    finally:
        if test_scenario == "inactive_pf":
            utils_net.Interface(sriov_test_obj.pf_name).up()
        sriov_test_obj.teardown_default()
