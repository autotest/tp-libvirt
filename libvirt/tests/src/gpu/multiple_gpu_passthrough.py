import re

from virttest import utils_misc
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_pcicontr
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_libvirt import libvirt_vfio

from provider.gpu import gpu_base
from provider.gpu import check_points


def setup_additional_gpu(vmxml, gpu_index, gpu_pci, base_pxb, base_root_port, pcie_root_slot, params):
    """
    Setup additional GPUs with controllers/iommu/hostdev

    :param vmxml: VM XML object
    :param gpu_index: GPU index (0-based)
    :param gpu_pci: GPU PCI address
    :param base_pxb: Base PXB controller index
    :param base_root_port: Base root port index
    :param pcie_root_slot: PCIe root slot
    :param params: Test parameters
    :return: Next available PCIe slot
    """
    pxb_index = base_pxb + (gpu_index * 2)
    root_port_index = pxb_index + 1

    # Create controllers for topology
    if params.get("pxb_ctrl_attrs"):
        pxb_attrs = eval(params.get("pxb_ctrl_attrs") % (str(pxb_index), pcie_root_slot))
        # Override busNr for additional GPUs to avoid conflicts
        if 'target' in pxb_attrs and 'busNr' in pxb_attrs['target']:
            busNr = 241 - (gpu_index * 10)
            pxb_attrs['target']['busNr'] = str(busNr)
        vmxml.add_device(libvirt_vmxml.create_vm_device_by_type("controller", pxb_attrs))

        root_port_attrs = eval(params.get("pcie_root_port_attrs") % (str(root_port_index), "%0#4x" % pxb_index))
        vmxml.add_device(libvirt_vmxml.create_vm_device_by_type("controller", root_port_attrs))
        last_index = root_port_index

        # Upstream port
        if params.get("upstream_port_attrs"):
            upstream_index = last_index + 1
            upstream_bus = "%0#4x" % last_index
            upstream_attrs = eval(params.get("upstream_port_attrs") % (upstream_index, upstream_bus))
            vmxml.add_device(libvirt_vmxml.create_vm_device_by_type("controller", upstream_attrs))
            last_index = upstream_index

        # Downstream port
        if params.get("downstream_port_attrs"):
            downstream_index = last_index + 1
            downstream_bus = "%0#4x" % last_index
            downstream_attrs = eval(params.get("downstream_port_attrs") % (downstream_index, downstream_index, downstream_bus))
            vmxml.add_device(libvirt_vmxml.create_vm_device_by_type("controller", downstream_attrs))
            last_index = downstream_index

        # Create iommu for this pxb
        iommu_dict = eval(params.get("iommu_dict") % str(pxb_index))
        if params.get("cmdqv_mode") == "mixed":
            iommu_dict['driver']['cmdqv'] = 'off'

        vmxml.add_device(libvirt_vmxml.create_vm_device_by_type("iommu", iommu_dict))
        gpu_bus = "%0#4x" % last_index

    else:
        gpu_bus = "%0#4x" % (base_root_port + gpu_index)

    # Get nodeset from cfg
    nodeset_map = eval(params.get("nodeset_map", "{}"))
    acpi_nodeset = {'nodeset': nodeset_map[gpu_index]}

    # Build hostdev for this GPU
    additional_gpu_dict = {
        'driver': {'driver_attr': {'iommufd': 'yes'}},
        'acpi': acpi_nodeset,
        'mode': 'subsystem', 'type': 'pci',
        'source': {'untyped_address': gpu_base.pci_to_addr(gpu_pci)},
        'managed': 'no',
        'address': {'attrs': {'type': 'pci', 'domain': '0x0000', 'bus': gpu_bus, 'slot': '0x00', 'function': '0x0'}}
    }
    vmxml.add_device(libvirt_vmxml.create_vm_device_by_type("hostdev", additional_gpu_dict))

    return "%0#4x" % (int(pcie_root_slot, 16) + 1)


def run(test, params, env):
    """
    Test multiple GPU passthrough to one guest
    """

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    num_gpus = int(params.get("num_gpus", "1"))
    cmdqv_mode = params.get("cmdqv_mode", "all_off")

    test.log.info("TEST_SETUP: Check GPU availability")
    host_gpus = gpu_base.get_gpus_info()
    available_gpu_count = len(host_gpus)
    if available_gpu_count < num_gpus:
        test.cancel(f"Test requires {num_gpus} GPU(s) but only {available_gpu_count} available")
    test.log.info(f"Found {available_gpu_count} GPU(s), test requires {num_gpus}")

    gpu_test = gpu_base.GPUTest(vm, test, params)
    dev_name = gpu_test.gpu_dev_name
    gpu_hostdev_dict = gpu_test.parse_hostdev_dict()
    gpu_managed_disabled = gpu_hostdev_dict.get('managed') != "yes"

    gpu_pcis = list(host_gpus.keys())[:num_gpus]
    test.log.info(f"Using GPUs: {gpu_pcis}")

    try:
        test.log.info("TEST_STEP: Bind GPUs on host")
        for gpu_pci in gpu_pcis:
            gpu_id = host_gpus[gpu_pci].get('ID')
            current_driver = host_gpus[gpu_pci].get('driver')
            if current_driver != 'nvgrace_gpu_vfio_pci':
                test.log.info(f"Binding {gpu_pci} to nvgrace-gpu-vfio-pci")
                utils_misc.cmd_status_output("modprobe -r nouveau", ignore_status=True)
                utils_misc.cmd_status_output("modprobe nvgrace-gpu-vfio-pci")
                utils_misc.cmd_status_output(f"sh -c \"echo '{gpu_id}' > /sys/bus/pci/drivers/nvgrace_gpu_vfio_pci/new_id\"")

        test.log.info("TEST_STEP: Configure VM XML")
        # setup_default handles first GPU + all controllers/iommu
        gpu_test.setup_default(dev_name=dev_name, test_hopper_gpu="yes")

        # For multiple GPUs, add additional hostdevs
        if num_gpus > 1:
            vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
            cntl_index = params.get("cntl_index")
            slot = libvirt_pcicontr.get_free_pci_slot(vmxml)

            for i, gpu_pci in enumerate(gpu_pcis[1:], start=1):
                slot = setup_additional_gpu(vmxml, i, gpu_pci, cntl_index["pxb_1"], cntl_index["pcie_root_port_1"], slot, params)

            vmxml.sync()

        test.log.info("TEST_STEP: Start VM")
        vm.start()
        vm_session = vm.wait_for_login(timeout=240)
        test.log.debug(f'VMXML of {vm_name}:\n{virsh.dumpxml(vm_name).stdout_text}')

        check_points.check_lspci(
            test,
            vm_session,
            eval(params.get("test_devices", '["3D"]')),
            expect_nic_exist=False
        )
        check_points.check_nvidia_smi(test, vm_session)

        test.log.info(f"TEST_STEP: Verify {num_gpus} GPU(s) in nvidia-smi")
        s, o = vm_session.cmd_status_output("nvidia-smi -L")
        gpu_count = len(re.findall(r'^GPU \d+:', o, re.MULTILINE))
        if gpu_count != num_gpus:
            test.fail(f"nvidia-smi shows {gpu_count} GPU(s), expected {num_gpus}")

        cmdqv_on_num = 0
        if cmdqv_mode == "all_on":
            cmdqv_on_num = num_gpus
        elif cmdqv_mode == "mixed":
            cmdqv_on_num = 1  # Only GPU0 has cmdqv on

        check_points.check_guest_cmdqv_dmesg(test, vm_session, expect_num=cmdqv_on_num)

        for gpu_pci in gpu_pcis:
            libvirt_vfio.check_vfio_pci(gpu_pci, exp_driver="nvgrace_gpu_vfio_pci")

        if vm_session:
            vm_session.close()

        test.log.info("TEST_STEP: Destroy VM")
        vm.destroy(gracefully=False)

        check_points.check_qemu_log(test, vm)

    finally:
        gpu_test.teardown_default(
            managed_disabled=gpu_managed_disabled,
            dev_name=dev_name
        )
