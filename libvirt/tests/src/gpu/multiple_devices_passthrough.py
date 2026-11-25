from virttest import utils_misc
from virttest import virsh
from virttest.utils_libvirt import libvirt_vfio

from provider.gpu import gpu_base
from provider.gpu import check_points
from provider.sriov import sriov_base
from provider.sriov import check_points as sriov_check_points


def run(test, params, env):
    """
    Test multiple devices passthrough (GPU + NIC)
    """

    def check_recovery():
        """
        Check environment recovery after VM destroy
        """
        test.log.info("TEST_STEP: Check GPU driver recovery")
        if not utils_misc.wait_for(
            lambda: libvirt_vfio.check_vfio_pci(
                gpu_dev_pci, gpu_managed_disabled, exp_driver="nvgrace_gpu_vfio_pci"), 10, 5):
            test.fail("GPU driver recovery failed")
        if gpu_managed_disabled:
            virsh.nodedev_reattach(
                gpu_dev_name, debug=True, ignore_status=False)
            libvirt_vfio.check_vfio_pci(
                gpu_dev_pci, True, exp_driver="nvgrace_gpu_vfio_pci")

        test.log.info("TEST_STEP: Check NIC driver and mac recovery")

        if not utils_misc.wait_for(
            lambda: libvirt_vfio.check_vfio_pci(
                nic_dev_pci, not nic_managed_disabled, True), 10, 5):
            test.fail("NIC driver recovery failed")
        if nic_managed_disabled:
            virsh.nodedev_reattach(
                nic_dev_name, debug=True, ignore_status=False)
            libvirt_vfio.check_vfio_pci(nic_dev_pci, True)
        sriov_check_points.check_mac_addr_recovery(
            sriov_test_obj.pf_name, nic_device_type, nic_iface_dict)

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    nic_dev_type = params.get("nic_type", "")
    nic_dev_source = params.get("dev_source", "")
    is_pf_hostdev = nic_dev_type == "nic_hostdev" and nic_dev_source.startswith("pf")

    if is_pf_hostdev:
        gpu_base.verify_pf_not_sole_uplink(test)
    sriov_test_obj = sriov_base.SRIOVTest(vm, test, params)
    if is_pf_hostdev:
        nic_dev_name = sriov_test_obj.pf_dev_name
        nic_dev_pci = sriov_test_obj.pf_pci
    else:
        nic_dev_name = sriov_test_obj.vf_dev_name
        nic_dev_pci = sriov_test_obj.vf_pci

    nic_iface_dict = sriov_test_obj.parse_iface_dict()
    nic_managed_disabled = nic_iface_dict.get('managed') != "yes"
    nic_device_type = "hostdev" if nic_dev_type == "nic_hostdev" else "interface"

    gpu_test = gpu_base.GPUTest(vm, test, params, sriov_helper=sriov_test_obj)
    gpu_dev_name = gpu_test.gpu_dev_name
    gpu_dev_pci = gpu_test.gpu_pci
    gpu_hostdev_dict = gpu_test.parse_hostdev_dict()
    gpu_managed_disabled = gpu_hostdev_dict.get('managed') != "yes"
    vm_session = None

    try:
        test.log.info("TEST_SETUP: Setup GPU and NIC devices")
        gpu_test.setup_nvgrace_host_driver()
        gpu_test.setup_default(dev_name=gpu_dev_name, test_hopper_gpu="yes")

        sriov_test_obj.setup_default(
            dev_name=nic_dev_name,
            managed_disabled=nic_managed_disabled,
            cleanup_ifaces="no")

        test.log.info("TEST_STEP: Start the VM")
        vm.start()
        vm_session = vm.wait_for_login(timeout=240)
        test.log.debug(
            f'VMXML of {vm_name}:\n{virsh.dumpxml(vm_name).stdout_text}')

        test.log.info("TEST_STEP: Verify GPU and NIC passthrough")
        check_points.check_lspci(
            test,
            vm_session,
            eval(params.get("test_devices", '["3D"]')),
            dev_iommu=bool(params.get("iommu_dict_2")),
        )
        check_points.check_nvidia_smi(test, vm_session)
        cmdqv_on_num = 0
        for key in ("cmdqv_dict", "cmdqv_dict_2"):
            if key == "cmdqv_dict_2" and not params.get("iommu_dict_2"):
                continue
            cmdqv_val = params.get(key)
            if cmdqv_val and eval(cmdqv_val).get("cmdqv") == "on":
                cmdqv_on_num += 1
        check_points.check_guest_cmdqv_dmesg(test, vm_session, expect_num=cmdqv_on_num)
        libvirt_vfio.check_vfio_pci(gpu_dev_pci, exp_driver="nvgrace_gpu_vfio_pci")

        test.log.info("TEST_STEP: Destroy VM")
        if vm_session:
            vm_session.close()
        vm.destroy()
        check_recovery()

    finally:
        test.log.info("TEST_TEARDOWN: Cleanup test environment")
        gpu_test.teardown_default(
            managed_disabled=gpu_managed_disabled,
            dev_name=gpu_dev_name)
        sriov_test_obj.teardown_default(
            managed_disabled=nic_managed_disabled,
            dev_name=nic_dev_name)
