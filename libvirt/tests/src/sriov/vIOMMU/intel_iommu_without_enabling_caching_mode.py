from virttest import virsh

from virttest.utils_test import libvirt

from provider.sriov import sriov_base


def run(test, params, env):
    """
    This case is to test hostdev interface with IOMMU device but no
    cache_mode=on.
    """
    attach_option = params.get("attach_option", "")
    err_msg = params.get("err_msg")
    dev_type = params.get("dev_type", "")
    iommu_dict = eval(params.get('iommu_dict', '{}'))

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)

    test_obj = sriov_base.SRIOVTest(vm, test, params)
    iface_dict = test_obj.parse_iface_dict()

    try:
        test_obj.setup_iommu_test(iommu_dict=iommu_dict, cleanup_ifaces=False)

        if attach_option and vm.is_alive():
            vm.destroy()
        elif not attach_option and not vm.is_alive():
            vm.start()
            vm.wait_for_login().close()

        iface_dev = test_obj.create_iface_dev(dev_type, iface_dict)
        test.log.info("TEST_STEP: Attach hostdev interface.")
        res = virsh.attach_device(vm.name, iface_dev.xml, debug=True,
                                  flagstr=attach_option)
        if attach_option:
            libvirt.check_exit_status(res)
            test.log.info("TEST_STEP: Start vm with hostdev.")
            res = virsh.start(vm.name, debug=True)

        libvirt.check_result(res, err_msg)

    finally:
        test_obj.teardown_iommu_test()
