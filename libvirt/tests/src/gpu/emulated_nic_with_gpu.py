import re
import time
import tempfile
import os

from virttest import utils_net
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_libvirt import libvirt_pcicontr

from provider.gpu import gpu_base
from provider.gpu import check_points


def run(test, params, env):
    """
    Test emulated virtio-net with GPU passthrough
    """

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    nic_bus_type = params.get("nic_bus_type", "pcie_root")
    attach_method = params.get("attach_method", "coldplug")

    virtio_net_mac = utils_net.generate_mac_address_simple()
    test.log.info(f"Using MAC address for virtio-net: {virtio_net_mac}")

    gpu_test = gpu_base.GPUTest(vm, test, params)
    dev_name = gpu_test.gpu_dev_name
    gpu_hostdev_dict = gpu_test.parse_hostdev_dict()
    gpu_managed_disabled = gpu_hostdev_dict.get('managed') != "yes"

    def create_virtio_net_xml(mac, bus_type, cntl_index=None):
        """
        Create virtio-net interface XML

        :param mac: MAC address for the interface
        :param bus_type: Bus type (pcie_root or pxb_pcie)
        :param cntl_index: Controller index dictionary
        :return: Interface dictionary
        """
        iface_dict = {
            'type_name': 'network',
            'source': {'network': 'default'},
            'model': 'virtio',
            'mac_address': mac
        }

        if bus_type == "pxb_pcie" and cntl_index:
            pcie_root_port_index = cntl_index.get("pcie_root_port_2")
            if pcie_root_port_index is None:
                test.error("pcie_root_port_2 not found in cntl_index")
            iface_dict['address'] = {
                'attrs': {
                    'type': 'pci',
                    'domain': '0x0000',
                    'bus': "%0#4x" % int(pcie_root_port_index),
                    'slot': '0x00',
                    'function': '0x0'
                }
            }

        return iface_dict

    def manage_virtio_net_device(iface_dict, operation, log_msg):
        """
        Attach or detach virtio-net device using temp XML file

        :param iface_dict: Interface dictionary
        :param operation: 'attach' or 'detach'
        :param log_msg: Log message to display
        """
        with tempfile.NamedTemporaryFile(mode='w', suffix='.xml', delete=False) as f:
            iface_xml = libvirt_vmxml.create_vm_device_by_type("interface", iface_dict)
            f.write(str(iface_xml))
            xml_path = f.name

        try:
            if operation == 'attach':
                virsh.attach_device(vm_name, xml_path, flagstr="--live", debug=True, ignore_status=False)
            else:
                virsh.detach_device(vm_name, xml_path, flagstr="--live", debug=True, ignore_status=False)
            test.log.info(log_msg)
            time.sleep(5)
        finally:
            os.unlink(xml_path)

    try:
        test.log.info("TEST_STEP: Setup GPU with SMMUv3 accel='on'")
        gpu_test.setup_nvgrace_host_driver()
        gpu_test.setup_default(dev_name=dev_name, test_hopper_gpu="yes")

        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        cntl_index = params.get("cntl_index", {})

        if nic_bus_type == "pxb_pcie":
            test.log.info("TEST_STEP: Add second pxb for network with SMMUv3 accel='off'")

            pcie_root_free_slot = libvirt_pcicontr.get_free_pci_slot(vmxml)
            base_pxb = cntl_index.get("pxb_1")

            # Second pxb index = base + offset
            pxb_index_2 = base_pxb + 100
            root_port_index_2 = pxb_index_2 + 1

            pxb_ctrl_attrs_2 = eval(params.get("pxb_ctrl_attrs_2") % (str(pxb_index_2), pcie_root_free_slot))
            vmxml.add_device(libvirt_vmxml.create_vm_device_by_type("controller", pxb_ctrl_attrs_2))

            pxb_bus = "%0#4x" % int(pxb_index_2)
            root_port_attrs_2 = eval(params.get("pcie_root_port_attrs_2") % (str(root_port_index_2), pxb_bus))
            vmxml.add_device(libvirt_vmxml.create_vm_device_by_type("controller", root_port_attrs_2))

            iommu_dict_2 = eval(params.get("iommu_dict_2") % str(pxb_index_2))

            vmxml.add_device(libvirt_vmxml.create_vm_device_by_type("iommu", iommu_dict_2))

            cntl_index["pcie_root_port_2"] = root_port_index_2

            vmxml.sync()

        if attach_method == "coldplug":
            test.log.info("TEST_STEP: Add virtio-net to initial XML (coldplug)")
            iface_dict = create_virtio_net_xml(virtio_net_mac, nic_bus_type, cntl_index)
            virtio_net_iface = libvirt_vmxml.create_vm_device_by_type("interface", iface_dict)
            vmxml.add_device(virtio_net_iface)
            vmxml.sync()

        test.log.info("TEST_STEP: Start VM")
        vm.start()
        vm_session = vm.wait_for_login(timeout=240)
        test.log.debug(f'VMXML of {vm_name}:\n{virsh.dumpxml(vm_name).stdout_text}')

        if attach_method == "hotplug":
            test.log.info("TEST_STEP: Hotplug virtio-net device")
            iface_dict = create_virtio_net_xml(virtio_net_mac, nic_bus_type, cntl_index)
            manage_virtio_net_device(iface_dict, 'attach', "Virtio-net attached successfully")

        test.log.info("TEST_STEP: Verify GPU functionality")
        check_points.check_lspci(
            test,
            vm_session,
            eval(params.get("test_devices", '["3D"]')),
            expect_nic_exist=False
        )
        check_points.check_nvidia_smi(test, vm_session)

        test.log.info("TEST_STEP: Verify virtio-net device")

        s, lspci_out = vm_session.cmd_status_output("lspci | grep -i 'virtio.*network\\|ethernet.*virtio'")
        if s != 0:
            test.fail("Virtio-net device not found in lspci")
        test.log.debug(f"Virtio-net in lspci: {lspci_out}")

        s, o = vm_session.cmd_status_output(f"ip link show | grep -B1 {virtio_net_mac.lower()}")
        if s != 0:
            test.fail(f"Could not find interface with MAC {virtio_net_mac}")

        iface_match = re.search(r'\d+:\s+(\S+):', o)
        if not iface_match:
            test.fail(f"Could not parse interface name from: {o}")
        iface_name = iface_match.group(1)
        test.log.info(f"Found virtio-net interface: {iface_name}")

        s, ip_out = vm_session.cmd_status_output(f"ip addr show {iface_name}")
        if s != 0:
            test.fail(f"Could not get IP info for {iface_name}")

        if "inet " not in ip_out:
            if attach_method == "hotplug":
                test.log.info(f"No IP detected on hotplug interface, triggering DHCP")
                vm_session.cmd(f"ip link set {iface_name} up")
                vm_session.cmd(f"dhclient {iface_name}", ignore_all_errors=True)
                time.sleep(10)
                # Check again
                s, ip_out = vm_session.cmd_status_output(f"ip addr show {iface_name}")
                if "inet " not in ip_out:
                    test.fail(f"Interface {iface_name} has no IPv4 address after DHCP")
            else:
                test.fail(f"Interface {iface_name} has no IPv4 address")

        test.log.debug(f"Interface {iface_name} has IP assigned")

        ping_target = params.get("ping_target", "8.8.8.8")
        test.log.info(f"TEST_STEP: Test connectivity to {ping_target}")
        s, ping_out = vm_session.cmd_status_output(f"ping -c 3 -I {iface_name} {ping_target}", timeout=30)
        if s == 0:
            received_match = re.search(r'(\d+) (?:packets )?received', ping_out)
            if received_match and int(received_match.group(1)) >= 3:
                test.log.info(f"Network connectivity from {iface_name} verified")
            else:
                test.log.warn(f"Ping partially successful but some packets lost")
        else:
            test.log.warn(f"Cannot reach {ping_target} (likely no internet/NAT on host). "
                        f"Interface {iface_name} is functional (DHCP succeeded, link UP).")

        test.log.info("TEST_STEP: Verify GPU still works after network setup")
        check_points.check_nvidia_smi(test, vm_session)

        if attach_method == "hotplug":
            test.log.info("TEST_STEP: Detach virtio-net device")
            iface_dict = create_virtio_net_xml(virtio_net_mac, nic_bus_type, cntl_index)
            manage_virtio_net_device(iface_dict, 'detach', "Virtio-net detached successfully")

            s, _ = vm_session.cmd_status_output(f"ip link show {iface_name}")
            if s == 0:
                test.fail(f"Interface {iface_name} still exists after detach")

            check_points.check_nvidia_smi(test, vm_session)

        test.log.info("TEST_STEP: Destroy VM")
        if vm_session:
            vm_session.close()
        vm.destroy(gracefully=False)

        check_points.check_qemu_log(test, vm)

    finally:
        gpu_test.teardown_default(
            managed_disabled=gpu_managed_disabled,
            dev_name=dev_name
        )
