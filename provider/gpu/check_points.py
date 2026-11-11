# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Red Hat
#
#   SPDX-License-Identifier: GPL-2.0
#
#   Author: dzheng@redhat.com
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

import re

from virttest import utils_sys
from virttest import utils_package

from provider.viommu import viommu_base
from provider.sriov import sriov_vfio


def check_device_iommu_group(test, vm_session, lspci_detail, pci_addr):
    """
    Check device IOMMU group in guest

    :param test: test object
    :param vm_session: VM session
    :param lspci_detail: lspci output detail
    :param pci_addr: PCI address of device
    """
    matches = re.findall(r"IOMMU group:\s*(\d+)", lspci_detail)
    if matches:
        iommu_grp_in_lspci = matches[0]
    else:
        test.error("Can't find IOMMU group info in guest lspci")

    iommu_group_dir = viommu_base.get_iommu_dev_dir(vm_session, pci_addr)
    status, device_num = vm_session.cmd_status_output("ls %s|wc -l" % iommu_group_dir)
    if status != 0:
        test.fail(f"Failed to list IOMMU group directory {iommu_group_dir}")
    expected_count = 2 if test.params.get("downstream_port_attrs") else 1
    if int(device_num) != expected_count:
        test.fail("Expect the iommu group dir %s to include %s device(s), but found %s" % (iommu_group_dir, expected_count, device_num))
    pattern = r"%s:\s*Adding to iommu group %s" % (pci_addr, iommu_grp_in_lspci)
    if not utils_sys.check_dmesg_output(pattern, expect=True, session=vm_session):
        test.fail("Failed to check iommu group for %s in guest dmesg" % pci_addr)

    test.log.debug("Verified device %s iommu group", pci_addr)


def check_gpu_kernel_driver(test, vm_session, gpu_lspci_detail):
    """
    Check GPU kernel driver in guest

    :param test: test object
    :param vm_session: VM session
    :param gpu_lspci_detail: GPU lspci output detail
    """
    found = re.search(r"Kernel driver in use:\s*nvidia", gpu_lspci_detail)
    if not found:
        test.fail("GPU kernel driver in guest lspci is unexpected")
    test.log.debug("Verified GPU kernel driver")


def check_qemu_log(test, vm):
    """
    Check qemu log for warnings or errors

    :param test: test object
    :param vm: VM object
    """
    local_qemu_log = utils_sys.get_qemu_log([vm], type="local")
    if not local_qemu_log:
        test.error("Failed to get local qemu-kvm log")
    local_qemu_log = local_qemu_log[0]["local"]
    found = re.search(r"qemu-kvm:\s*warning|qemu-kvm:\s*error", local_qemu_log)
    if found:
        test.fail("qemu-kvm warning or errors are detected in qemu-kvm log")
    test.log.debug("Verified qemu log")


def check_guest_cmdqv_dmesg(test, vm_session, expect_num=None):
    """
    Check guest dmesg for vCMDQ device allocation

    :param test: test object
    :param vm_session: VM session
    :param expect_num: Expected number of vCMDQ devices
    """
    patterns = [
        r"arm-smmu-v3.*:\s*found companion CMDQV device:",
        r"arm-smmu-v3.*:\s*allocated.*for vcmdq0",
        r"arm-smmu-v3.*:\s*allocated.*for vcmdq1"
    ]
    dmesg = vm_session.cmd_output("dmesg")
    for pat in patterns:
        matches = re.findall(pat, dmesg)
        cmdqv_num = len(matches) if matches else 0
        if cmdqv_num != expect_num:
            test.fail("Expect %d cmdqv devices for pattern %s, but found %d" % (expect_num, pat, cmdqv_num))
    test.log.debug("Verified guest dmesg for cmdqv")


def check_lspci(test, vm_session, test_devices, dev_iommu=True, expect_nic_exist=True):
    """
    Check GPU and NIC devices in guest

    :param test: test object
    :param vm_session: VM session
    :param test_devices: List of device types to check
    :param dev_iommu: Whether to check IOMMU group
    :param expect_nic_exist: Whether NIC is expected to exist
    """
    if not utils_package.package_install(["pciutils"], vm_session):
        test.error("Unable to install pciutils in guest")

    def _check_lspci(pci_addr, is_gpu=True, dev_iommu=True):
        """
        Check lspci detail for a device and verify IOMMU/driver

        :param pci_addr: PCI address of the device
        :param is_gpu: Whether to check GPU kernel driver
        :param dev_iommu: Whether to check device IOMMU group
        """
        status, lspci_detail = vm_session.cmd_status_output("lspci -vvs %s" % pci_addr)
        test.log.debug("The lspci info for device %s in the guest:\n%s", pci_addr, lspci_detail)
        if dev_iommu:
            check_device_iommu_group(test, vm_session, lspci_detail, pci_addr)
        if is_gpu:
            check_gpu_kernel_driver(test, vm_session, lspci_detail)
    status, lspci_output = vm_session.cmd_status_output("lspci")
    test.log.debug("The output of lspci in the guest:\n%s", lspci_output)
    test_devices_pci_addrs = viommu_base.get_devices_pci(vm_session, test_devices)

    for nic_device_pat in ["Network Connection", "Ethernet Controller Virtual Function", "Mellanox Technologies"]:
        nic_pci_addrs = test_devices_pci_addrs.get(nic_device_pat)
        if nic_pci_addrs:
            break

    if not expect_nic_exist:
        if nic_pci_addrs:
            test.error("The NIC %s is unexpected in guest lspci" % nic_pci_addrs)
        else:
            test.log.debug("Verified the NIC is not found in guest lspci")
    else:
        if not nic_pci_addrs:
            test.error("The NIC is not found in guest lspci")
        else:
            test.log.debug("Verified the NIC %s is found in guest lspci", nic_pci_addrs)

    gpu_pci_addrs = test_devices_pci_addrs.get("3D")
    if gpu_pci_addrs:
        test.log.debug("GPU pci address: %s", gpu_pci_addrs)
        for gpu_pci_addr in gpu_pci_addrs:
            _check_lspci(gpu_pci_addr)
    test.log.debug("NIC pci address: %s", nic_pci_addrs)
    if nic_pci_addrs:
        for nic_pci_addr in nic_pci_addrs:
            _check_lspci(nic_pci_addr, is_gpu=False, dev_iommu=dev_iommu)

def check_nvidia_smi(test, vm_session):
    """
    Run nvidia-smi command and check GPU device's info

    :param vm_session: vm's session
    """
    status, output = vm_session.cmd_status_output("nvidia-smi")
    if status or not re.search(r"CUDA.*Version:", output):
        test.fail("Failed to run nvidia-smi command. Status: %s, output: %s."
                  % (status, output))
    test.log.debug("Verified nvidia-smi works well")
