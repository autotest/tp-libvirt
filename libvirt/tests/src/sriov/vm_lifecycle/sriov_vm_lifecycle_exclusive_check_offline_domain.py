from provider.sriov import sriov_base

from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml import xcepts
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Confirm a device occupied can not be used by others
    """
    def run_test():
        test.log.info("TEST_STEP1: Attach a hostdev interface/device to VM")
        iface_dev = sriov_test_obj.create_iface_dev(dev_type, iface_dict)
        libvirt.add_vm_device(vm_xml.VMXML.new_from_dumpxml(vm_name), iface_dev)
        test.log.info("TEST_STEP2: Start the VM")
        iface_dev2 = sriov_test_obj.create_iface_dev(dev_type2, iface_dict2)
        try:
            libvirt.add_vm_device(vm_xml.VMXML.new_from_dumpxml(vm_name), iface_dev2)
        except xcepts.LibvirtXMLError as details:
            if define_err and err_msg:
                if not str(details).count(err_msg):
                    test.fail("Incorrect error message, it should be '{}', but "
                              "got '{}'.".format(err_msg, details))
            else:
                test.fail(details)
        else:
            if define_err:
                test.fail("Defining the VM should fail, but run successfully!")
            test.log.info("TEST_STEP2: Start the VM")
            result = virsh.start(vm.name, debug=True)
            libvirt.check_exit_status(result, True)
            if err_msg:
                libvirt.check_result(result, err_msg)

    dev_type = params.get("dev_type", "")
    dev_type2 = params.get("dev_type2", "")

    define_err = "yes" == params.get("define_err")
    err_msg = params.get("err_msg")

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    sriov_test_obj = sriov_base.SRIOVTest(vm, test, params)
    iface_dict = sriov_test_obj.parse_iface_dict()

    test_dict = {'iface_dict': params.get("iface_dict2"),
                 'hostdev_dict': params.get("hostdev_dict2")}
    sriov_test_obj2 = sriov_base.SRIOVTest(vm, test, test_dict)
    iface_dict2 = sriov_test_obj2.parse_iface_dict()

    try:
        sriov_test_obj.setup_default()
        run_test()

    finally:
        sriov_test_obj.teardown_default()
