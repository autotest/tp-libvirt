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

import time

from virttest import utils_misc
from virttest import utils_net
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vfio
from virttest.utils_libvirt import libvirt_vmxml

from provider.gpu import gpu_base
from provider.gpu import check_points
from provider.sriov import sriov_base
from provider.sriov import check_points as sriov_check_points


def run(test, params, env):
    """
    Plug/unplug a NIC device with a GPU passthrough

    Steps:
    1. Setup GPU and NIC devices
    2. Execute plug/unplug operations based on test variants:
       - coldplug/coldunplug: Add NIC before VM start, remove after VM shutdown
       - coldplug/hotunplug: Add NIC before VM start, remove while VM running
       - hotplug/coldunplug: Add NIC while VM running, remove after VM shutdown
       - hotplug/hotunplug: Add NIC while VM running, remove while VM running
    3. Verify devices and functionality
    4. Check environment recovery
    """
    def plug_nic(plug_opt=""):
        if nic_dev_type == "network_interface":
            test.log.info("TEST_STEP: Cold/Hotplug network interface to VM")            
            opts = "network %s %s" % (nic_network_dict['name'], plug_opt)
            virsh.attach_interface(
                vm.name,
                opts,
                debug=True,
                ignore_status=False
            )
        else:
            test.log.info("TEST_STEP: Cold/Hotplug NIC hostdev to VM")
            nic_dev = sriov_test_obj.create_iface_dev(nic_dev_type, nic_iface_dict)
            params["nic_dev"] = nic_dev
            virsh.attach_device(
                vm.name,
                nic_dev.xml,
                flagstr=plug_opt,
                debug=True,
                ignore_status=False
            )

    def unplug_nic(unplug_opt=""):
        if nic_device_type == "interface":        
            test.log.info("TEST_STEP: Detach the NIC hostdev interface.")
            virsh.detach_interface(
                vm.name,
                "hostdev %s" % unplug_opt,
                debug=True,
                ignore_status=False,
                wait_for_event=(unplug_opt == "")
            )
        else:
            test.log.info("TEST_STEP: Detach the NIC hostdev device.")
            virsh.detach_device(
                vm.name, params.get("nic_dev").xml,
                debug=True,
                flagstr=unplug_opt,
                wait_for_event=(unplug_opt == ""),
                event_timeout=30,
                ignore_status=False
            )
        # cur_devices = vm_xml.VMXML.new_from_dumpxml(vm.name)\
        #     .devices.by_device_tag(nic_device_type)
        # if cur_devices:
        #     test.fail("Got %s device(%s) after hotunplugging!"
        #               % (nic_device_type, cur_devices))        
        
    def check_nic_driver_recovery():
        """
        Check NIC driver recovery after device removal
        """
        test.log.info("TEST_STEP: Check NIC driver and mac recovery")
        if not utils_misc.wait_for(
            lambda: libvirt_vfio.check_vfio_pci(
                nic_dev_pci, not nic_managed_disabled, True), 10, 5):
            test.fail("NIC driver recovery failed!")
        if nic_managed_disabled:
            virsh.nodedev_reattach(nic_dev_name, debug=True, ignore_status=False)
            libvirt_vfio.check_vfio_pci(nic_dev_pci, True)
        sriov_check_points.check_mac_addr_recovery(
            sriov_test_obj.pf_name, nic_device_type, nic_iface_dict)

    def update_nic_bus_info(nic_dev_dict):
        cntl_index_dict = gpu_test.params["cntl_index"]
        pcie_root_port_index = int(cntl_index_dict.get("pcie_root_port_2"))
        #iface_dict = eval(nic_dev_dict % ("%0#4x" % int(pcie_root_port_index)))
        test.log.info("nic_dev_dict=%s", nic_dev_dict)
        nic_dev_dict.get("address").get("attrs")["bus"] = "%0#4x" % int(pcie_root_port_index)
        test.log.info("nic_dev_dict=%s", nic_dev_dict)

    def run_test():
        """
        Main test flow for plug/unplug NIC device with GPU passthrough
        """
        vm_session = None

        if coldplug == "yes":
            plug_nic(plug_opt="--config")

        test.log.info("TEST_STEP: Start the VM")
        vm.start()
        vm_session = vm.wait_for_serial_login(timeout=240)
        test.log.debug(
            f'VMXML of {vm_name}:\n{virsh.dumpxml(vm_name).stdout_text}')

        if hotplug == "yes":
            plug_nic()

        test.log.info("TEST_STEP: Check NIC device - mac address")
        sriov_check_points.check_mac_addr(
            vm_session, vm.name, nic_device_type, nic_iface_dict)
    
        test.log.info("TEST_STEP: Check GPU and NIC devices in VM")
        time.sleep(5)
        test_devices = eval(params.get("test_devices"))
        check_points.check_lspci(
            test,
            vm_session,
            test_devices,
            dev_iommu=True if params.get("iommu_dict_2") else False,
        )
        check_points.check_nvidia_smi(test, vm_session)
        cmdqv_on_num = int(params.get("cmdqv_on_num", "1"))
        check_points.check_guest_cmdqv_dmesg(test, vm_session, expect_num=cmdqv_on_num)
        libvirt_vfio.check_vfio_pci(gpu_dev_pci, exp_driver="nvgrace_gpu_vfio_pci")
        test.log.info("Verify: GPU driver is nvgrace_gpu_vfio_pci - PASS")

        if hotunplug == "yes":
            unplug_nic()
            check_nic_driver_recovery()
            check_points.check_nvidia_smi(test, vm_session)
            # check lspci does not include the nic
            test_devices.remove("3D")            
            check_points.check_lspci(
                test,
                vm_session,
                test_devices,
                expect_nic_exist=False
            )
            # Check guest xml does not include this nic hostdev / nic interface

        if coldunplug == "yes":
            test.log.info("TEST_STEP: Shutdown VM for coldunplug NIC")
            if vm_session:
                vm_session.close()
                vm_session = None
            vm.destroy(gracefully=True)
            unplug_nic(unplug_opt="--config")
            check_nic_driver_recovery()

        if vm_session:
            vm_session.close()
        # check_points.check_qemu_log()

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    nic_dev_type = params.get("dev_type", "")
    nic_dev_source = params.get("dev_source", "")
    coldplug = params.get("coldplug", "no")
    coldunplug = params.get("coldunplug", "no")
    hotplug = params.get("hotplug", "no")
    hotunplug = params.get("hotunplug", "no")

    sriov_test_obj = sriov_base.SRIOVTest(vm, test, params)
    if nic_dev_type == "nic_hostdev" and nic_dev_source.startswith("pf"):
        nic_dev_name = sriov_test_obj.pf_dev_name
        nic_dev_pci = sriov_test_obj.pf_pci
    else:
        nic_dev_name = sriov_test_obj.vf_dev_name
        nic_dev_pci = sriov_test_obj.vf_pci

    nic_iface_dict = sriov_test_obj.parse_iface_dict()
    nic_network_dict = sriov_test_obj.parse_network_dict()
    test.log.debug("nic_network_dict=%s", nic_network_dict)
    test.log.debug("nic_iface_dict=%s", nic_iface_dict)
    if nic_network_dict:
        nic_managed_disabled = nic_network_dict.get("forward").get('managed') != "yes"
    else:
        nic_managed_disabled = nic_iface_dict.get('managed') != "yes"
    
    nic_device_type = "hostdev" if nic_dev_type == "nic_hostdev" else "interface"

    gpu_test = gpu_base.GPUTest(vm, test, params, sriov_helper=sriov_test_obj)
    gpu_dev_name = gpu_test.gpu_dev_name
    gpu_dev_pci = gpu_test.gpu_pci
    gpu_hostdev_dict = gpu_test.parse_hostdev_dict()
    gpu_managed_disabled = gpu_hostdev_dict.get('managed') != "yes"

    try:
        test.log.info("TEST_SETUP: Setup GPU and NIC devices")
        gpu_test.setup_default(dev_name=gpu_dev_name, test_hopper_gpu="yes", plug_nic=True)

        sriov_test_obj.setup_default(
            dev_name=nic_dev_name,
            managed_disabled=nic_managed_disabled,
            network_dict=nic_network_dict,
            cleanup_ifaces="no")
        if nic_dev_type != "network_interface": 
            update_nic_bus_info(nic_iface_dict)
        run_test()

    finally:
        test.log.info("TEST_TEARDOWN: Cleanup test environment")
        gpu_test.teardown_default(
            managed_disabled=gpu_managed_disabled,
            dev_name=gpu_dev_name)
        sriov_test_obj.teardown_default(
            managed_disabled=nic_managed_disabled,
            dev_name=nic_dev_name,
            network_dict=nic_network_dict)
