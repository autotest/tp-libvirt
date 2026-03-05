#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Red Hat
#
#   SPDX-License-Identifier: GPL-2.0
#
#   Author: dzheng <dzheng@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

from virttest import utils_misc
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_libvirt import libvirt_vfio
from virttest.utils_test import libvirt

from provider.gpu import gpu_base
from provider.gpu import check_points
from provider.sriov import sriov_base
from provider.sriov import check_points as sriov_check_points


def run(test, params, env):
    """
    Start vm with GPU device and NIC device (VF/PF) passthrough

    Steps:
    1. Setup GPU hostdev device
    2. Setup NIC hostdev device or interface (VF/PF)
    3. Start the VM
    4. Check devices using unified check_lspci() entry
    5. Check NIC-specific features (mac address, network accessibility)
    6. Destroy VM
    7. Check environment recovery (driver, mac address)
    """
    def setup_gpu_device():
        """
        Setup GPU hostdev device
        """
        test.log.info("TEST_STEP: Configure GPU hostdev device")
        gpu_hostdev_dict = gpu_test.parse_hostdev_dict()
        libvirt_vmxml.modify_vm_device(
            vm_xml.VMXML.new_from_dumpxml(vm_name), "hostdev",
            gpu_hostdev_dict)

    def setup_nic_device():
        """
        Setup NIC hostdev device or interface
        """
        test.log.info("TEST_STEP: Configure NIC hostdev/interface device")
        nic_iface_dict = sriov_test_obj.parse_iface_dict()
        nic_iface_dev = sriov_test_obj.create_iface_dev(
            nic_dev_type, nic_iface_dict)
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        libvirt.add_vm_device(vmxml, nic_iface_dev)
        return nic_iface_dict

    def check_devices_in_guest():
        """
        Check GPU and NIC devices in guest using unified checkpoint
        """
        test.log.info("TEST_STEP: Check devices via lspci")
        test_devices = ["3D", "Ethernet"]
        check_lspci_args = {"test_devices": str(test_devices)}
        check_points.check_lspci(test, vm_session, **check_lspci_args)

    def check_nic_specific():
        """
        Check NIC-specific features
        """
        test.log.info("TEST_STEP: Check NIC-specific features")
        nic_iface_dict_updated = nic_iface_dict.copy()
        if nic_dev_type == "network_interface":
            nic_iface_dict_updated.update({'type_name': 'hostdev'})

        sriov_check_points.comp_hostdev_xml(
            vm, nic_device_type, nic_iface_dict_updated)
        sriov_check_points.check_mac_addr(
            vm_session, vm.name, nic_device_type, nic_iface_dict)

        if 'vlan' in nic_iface_dict:
            sriov_check_points.check_vlan(
                sriov_test_obj.pf_name, nic_iface_dict)
        else:
            check_ping_time = params.get("check_ping_time")
            if check_ping_time == "yes":
                sriov_check_points.check_vm_network_accessed(vm_session)

    def check_recovery():
        """
        Check environment recovery after VM destroy
        """
        test.log.info("TEST_STEP: Check GPU driver recovery")
        if not utils_misc.wait_for(
            lambda: libvirt_vfio.check_vfio_pci(
                gpu_dev_pci, not gpu_managed_disabled, True), 10, 5):
            test.fail("GPU driver recovery failed!")
        if gpu_managed_disabled:
            virsh.nodedev_reattach(
                gpu_dev_name, debug=True, ignore_status=False)
            libvirt_vfio.check_vfio_pci(
                gpu_dev_pci, True, exp_driver="nvgrace_gpu_vfio_pci")

        test.log.info("TEST_STEP: Check NIC driver and mac recovery")
        sriov_check_points.check_vlan(
            sriov_test_obj.pf_name, nic_iface_dict, True)
        if not utils_misc.wait_for(
            lambda: libvirt_vfio.check_vfio_pci(
                nic_dev_pci, not nic_managed_disabled, True), 10, 5):
            test.fail("NIC driver recovery failed!")
        if nic_managed_disabled:
            virsh.nodedev_reattach(
                nic_dev_name, debug=True, ignore_status=False)
            libvirt_vfio.check_vfio_pci(nic_dev_pci, True)
        sriov_check_points.check_mac_addr_recovery(
            sriov_test_obj.pf_name, nic_device_type, nic_iface_dict)

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    nic_dev_type = params.get("dev_type", "")
    nic_dev_source = params.get("dev_source", "")

    gpu_test = gpu_base.GPUTest(vm, test, params)
    gpu_dev_name = gpu_test.gpu_dev_name
    gpu_dev_pci = gpu_test.gpu_pci
    gpu_hostdev_dict = gpu_test.parse_hostdev_dict()
    gpu_managed_disabled = gpu_hostdev_dict.get('managed') != "yes"

    sriov_test_obj = sriov_base.SRIOVTest(vm, test, params)
    if nic_dev_type == "hostdev_device" and nic_dev_source.startswith("pf"):
        nic_dev_name = sriov_test_obj.pf_dev_name
        nic_dev_pci = sriov_test_obj.pf_pci
    else:
        nic_dev_name = sriov_test_obj.vf_dev_name
        nic_dev_pci = sriov_test_obj.vf_pci

    nic_iface_dict = sriov_test_obj.parse_iface_dict()
    nic_managed_disabled = nic_iface_dict.get('managed') != "yes"
    nic_device_type = "hostdev" if nic_dev_type == "hostdev_device" else "interface"

    vm_session = None

    try:
        test.log.info("TEST_SETUP: Setup GPU and NIC devices")
        gpu_test.setup_default(
            dev_name=gpu_dev_name,
            managed_disabled=gpu_managed_disabled)
        sriov_test_obj.setup_default(
            dev_name=nic_dev_name,
            managed_disabled=nic_managed_disabled,
            cleanup_ifaces="no")

        setup_gpu_device()
        nic_iface_dict = setup_nic_device()

        test.log.info("TEST_STEP: Start the VM")
        vm.start()
        vm_session = vm.wait_for_login(timeout=240)
        test.log.debug(
            f'VMXML of {vm_name}:\n{virsh.dumpxml(vm_name).stdout_text}')

        check_devices_in_guest()
        check_nic_specific()

        test.log.info("TEST_STEP: Destroy VM")
        vm.destroy(gracefully=False)

        check_recovery()

    finally:
        if vm_session:
            vm_session.close()
        test.log.info("TEST_TEARDOWN: Cleanup test environment")
        gpu_test.teardown_default(
            managed_disabled=gpu_managed_disabled,
            dev_name=gpu_dev_name)
        sriov_test_obj.teardown_default(
            managed_disabled=nic_managed_disabled,
            dev_name=nic_dev_name)
