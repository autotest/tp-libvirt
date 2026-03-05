# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Red Hat
#
#   SPDX-License-Identifier: GPL-2.0
#
#   Author: dzheng@redhat.com
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

from provider.viommu import viommu_base


def check_gpu_numa_in_lspci(lspci_info):
    pass


def check_gpu_iommu_group(test, vm_session, gpu_lspci_detail, gpu_pci_addr):
    matches = re.findall("IOMMU group:\s*(\d+)", lspci_info)
    if matches:
        iommu_grp_in_lspci = matches[0]
    else:
        test.error("Can't find IOMMU group info in guest lspci")
    # /sys/kernel/iommu_groups/1/devices
    #viommu_base.get_devices_pci(vm_session, )
    iommu_group_dir = viommu_base.get_iommu_dev_dir(vm_session, gpu_pci_addr)
    _, iommu_group_in_sys = vm_session.cmd_status_output("cut -d'/' -f 5 %s" % iommu_group_dir)
    if iommu_group_in_sys != iommu_grp_in_lspci:
        test.fail("IOMMU group in lspci (%s) does not match the value in sys file (%s)" % (iommu_grp_in_lspci, iommu_group_in_sys))
    status, device_num = vm_session.cmd_status_output("ls %s|wc -l" % iommu_group_dir)
    if int(device_num) != 1:
        test.fail("Expect the iommu group dir %s only include one device %s, but found %s devices" % (iommu_group_dir, gpu_pci_addr, device_num) )


def check_gpu_kernel_driver(test, vm_session, gpu_lspci_detail):
    status, _ = vm_session.cmd_status_output("grep \"Kernel driver in use:\s*nvidia\" %s" % gpu_lspci_detail)
    if status:
        test.fail("GPU kernel driver in guest lspci is unexpected")


def check_lspci(test, vm_session, **dargs):
    """
    Check GPU device in guest

    :param vm: vm object
    :param status_error: True if expect not existing, otherwise False
    """

    status, lspci_output = vm_session.cmd_status_output("lspci")
    test.log.debug("The output of lspci in the guest:\n%s", lspci_output)
    #gpu_pci_addr = vm_session.cmd_status_output("lspci |grep \"3D controller\" |cut -d\" \" -f1")
    test_devices = eval(dargs.get("test_devices", ["3D"]))
    test_devices_pci_addrs = viommu_base.get_devices_pci(vm_session, test_devices)
    gpu_pci_addr = test_devices_pci_addrs["3D"]
    status, gpu_detail = vm_session.cmd_status_output("lspci -vvs %s" % gpu_pci_addr)
    test.log.debug("The lspci info for GPU in the guest:\n%s", gpu_detail)

    check_gpu_iommu_group(test, vm_session, gpu_lspci_detail, gpu_pci_addr)
    check_gpu_kernel_driver(test, vm_session, gpu_lspci_detail)

    # Check Node node
    check_gpu_numa_in_lspci()
    vm_session.close()


def check_nvidia_smi(test, vm_session):
    """
    Run nvidia-smi command and check GPU device's info

    :param vm_session: vm's session
    """
    gpu_pci = get_gpu_pci(session=vm_session)
    s, o = vm_session.cmd_status_output("nvidia-smi -q")
    if s or not re.search(gpu_pci, o):
        self.test.fail("Failed to run nvidia-smi command. Status: %s, output: %s."
                        % (s, o))
