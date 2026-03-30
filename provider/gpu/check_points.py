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

from avocado.utils import process

from virttest import utils_sys
from virttest import utils_package

from provider.viommu import viommu_base
from provider.gpu import gpu_base
from provider.sriov import sriov_vfio


def check_numa_node_in_lspci(lspci_detail):
    pass


def check_device_iommu_group(test, vm_session, lspci_detail, pci_addr):
    matches = re.findall("IOMMU group:\s*(\d+)", lspci_detail)
    if matches:
        iommu_grp_in_lspci = matches[0]
    else:
        test.error("Can't find IOMMU group info in guest lspci")

    iommu_group_dir = viommu_base.get_iommu_dev_dir(vm_session, pci_addr)
    # _, iommu_group_in_sys = vm_session.cmd_status_output("cut -d'/' -f 5 %s" % iommu_group_dir)
    # if iommu_group_in_sys != iommu_grp_in_lspci:
    #     test.fail("IOMMU group in lspci (%s) does not match the value in sys file (%s)" % (iommu_grp_in_lspci, iommu_group_in_sys))
    status, device_num = vm_session.cmd_status_output("ls %s|wc -l" % iommu_group_dir)
    if int(device_num) != 1:
        test.fail("Expect the iommu group dir %s only include one device %s, but found %s devices" % (iommu_group_dir, pci_addr, device_num) )
    pattern = r"%s:\s*Adding to iommu group %s" % (pci_addr, iommu_grp_in_lspci)
    if not utils_sys.check_dmesg_output(pattern, expect=True, session=vm_session):
        test.fail("Failed to check iommu group for %s in guest dmesg" % pci_addr)

    test.log.debug("Verify device %s iommu group: PASS", pci_addr)


def check_gpu_kernel_driver(test, vm_session, gpu_lspci_detail):
    found = bool(re.findall("Kernel driver in use:\s*nvidia", gpu_lspci_detail))
    #status, _ = vm_session.cmd_status_output("grep \"Kernel driver in use:\s*nvidia\" %s" % gpu_lspci_detail)
    if not found:
        test.fail("GPU kernel driver in guest lspci is unexpected")
    test.log.debug("Verify GPU kernel driver: PASS")


def check_qemu_log(test, vm):
    local_qemu_log = utils_sys.get_qemu_log([vm], type="local")
    if not local_qemu_log:
        test.error("Failed to get local qemu-kvm log")
    local_qemu_log = local_qemu_log[0]["local"]    
    found = bool(re.findall(r"qemu-kvm:\s*warning|qemu-kvm:\s*error", local_qemu_log))
    if found:
        test.fail("qemu-kvm warning or errors are detected in qemu-kvm log")    
    test.log.debug("Verify qemu log: PASS")


def check_guest_cmdqv_dmesg(test, vm_session, expect_num=None):
    patterns = [
        "arm-smmu-v3.*:\s*found companion CMDQV device:",
        "arm-smmu-v3.*:\s*allocated.*for vcmdq0",
        "arm-smmu-v3.*:\s*allocated.*for vcmdq1"
    ]
    dmesg = vm_session.cmd ("dmesg")
    for pat in patterns:
        matches = re.findall(pat, dmesg)
        cmdqv_num = len(matches) if matches else 0
        if cmdqv_num != expect_num:
            test.fail("Expect %d cmdqv devices for pattern %s, but found %d" % (expect_num, pat, cmdqv_num))
    test.log.debug("Verify guest dmesg for cmdqv: PASS")


def check_lspci(test, vm_session, test_devices, dev_iommu=True, expect_nic_exist=True):
    """
    Check GPU device in guest

    :param vm: vm object
    :param status_error: True if expect not existing, otherwise False
    """
    def _check_lspci(pci_addr, is_gpu=True, dev_iommu=True):
        status, lspci_detail = vm_session.cmd_status_output("lspci -vvs %s" % pci_addr)
        test.log.debug("The lspci info for device %s in the guest:\n%s", pci_addr, lspci_detail)
        if dev_iommu:
            check_device_iommu_group(test, vm_session, lspci_detail, pci_addr)
        if is_gpu:
            check_gpu_kernel_driver(test, vm_session, lspci_detail)
            # Check Node node
            # check_numa_node_in_lspci(lspci_detail)
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
            test.log.debug("Verify the NIC is not found in guest lspci - PASS")
    else:
        if not nic_pci_addrs:
            test.error("The NIC is not found in guest lspci")
        else:
            test.log.debug("Verify the NIC %s is found in guest lspci - PASS", nic_pci_addrs)

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
    if status or not re.search("CUDA Version:", output):
        test.fail("Failed to run nvidia-smi command. Status: %s, output: %s."
                  % (status, output))
    test.log.debug("Verify nvdia-smi works well - PASS")

def check_vm_network(test, vm_session, iface_name):
    vm_iface = utils_misc.wait_for(
    lambda: utils_net.get_linux_ifname(vm_session, mac_addr),
    timeout=240, first=10)
    ping_dest = "8.8.8.8"
    if not utils_package.package_install("ethtool", vm_session):
        test.error(f"Unable to install ethtool in guest!")
    if not sriov_vfio.is_linked(iface_name, vm_session):
        test.log.debug("Ignore network connectivity check due to the interface has no link.")
        return
    status, output = utils_net.ping(
        dest=ping_dest,
        interface=iface_name,
        count="10",
        session=vm_session
    )
    if status:
        raise exceptions.TestFail("Failed to ping %s! status: %s, "
                                  "output: %s." % (ping_dest, status, output))
