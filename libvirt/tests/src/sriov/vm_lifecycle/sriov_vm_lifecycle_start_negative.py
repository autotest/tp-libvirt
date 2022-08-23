from provider.sriov import sriov_base

from virttest import utils_net
from virttest import virsh

from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml import xcepts


def run(test, params, env):
    """
    Start vm with a hostdev device or hostdev interface(negative)
    """
    def run_test():
        """
        Start vm with an interface/device of hostdev type
        """
        if test_scenario == "inactive_pf":
            utils_net.Interface(sriov_test_obj.pf_name).down()
        elif test_scenario == "inactive_network":
            virsh.net_destroy(network_dict['name'], debug=True)

        test.log.info("TEST_STEP1: Attach a hostdev interface/device to VM")
        iface_dev = sriov_test_obj.create_iface_dev(dev_type, iface_dict)
        try:
            libvirt.add_vm_device(vm_xml.VMXML.new_from_dumpxml(vm_name),
                                  iface_dev)
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
    test_scenario = params.get("test_scenario")

    define_err = "yes" == params.get("define_err")
    err_msg = params.get("err_msg")
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    sriov_test_obj = sriov_base.SRIOVTest(vm, test, params)
    test_dict = sriov_test_obj.parse_iommu_test_params()
    iface_dict = sriov_test_obj.parse_iface_dict()
    network_dict = sriov_test_obj.parse_network_dict()
    try:
        if test_dict['iommu_dict']:
            sriov_test_obj.setup_iommu_test(**test_dict)
        else:
            sriov_test_obj.setup_default(network_dict=network_dict)
        run_test()

    finally:
        if test_scenario == "inactive_pf":
            utils_net.Interface(sriov_test_obj.pf_name).up()
        sriov_test_obj.teardown_default(network_dict=network_dict)
