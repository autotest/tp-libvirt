import os
import time

from virttest import data_dir
from virttest import utils_disk
from virttest import utils_net
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_disk
from virttest.utils_libvirt import libvirt_vmxml

from provider.sriov import sriov_base
from provider.viommu import viommu_base
from provider.sriov import check_points as sriov_check_points


def run(test, params, env):
    """
    Hotplug devices with iommu enabled to a vm with an iommu device
    """
    def detach_dev(device_type, dev_iommu_info, vm_session):
        """
        Detach device and check iommu group if needed

        :param device_type: Device type
        :param dev_iommu_info: Device's iommu group info
        :param vm_session: VM session
        """
        dev_obj = vm_xml.VMXML.new_from_dumpxml(vm_name).get_devices(device_type)[-1]
        virsh.detach_device(vm_name, dev_obj.xml, wait_for_event=True,
                            debug=True, ignore_status=False)
        if not dev_iommu_info:
            return
        for dev_info in dev_iommu_info:
            if dev_info:
                res = vm_session.cmd_status_output('ifconfig')
                test.log.debug(res)
                s, o = vm_session.cmd_status_output("ls %s" % dev_info)
                test.log.debug(f"{device_type} iommu check: {o}")
                if not s:
                    test.fail("The %s should be removed from the iommu group" % device_type)

    cleanup_ifaces = params.get("cleanup_ifaces", "yes")
    disk_dict = eval(params.get('disk_dict', '{}'))

    ping_dest = params.get('ping_dest')
    iommu_dict = eval(params.get('iommu_dict', '{}'))
    test_devices = eval(params.get("test_devices", "[]"))
    need_sriov = "yes" == params.get("need_sriov", "no")

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    sroiv_test_obj = None
    new_disk_path = None

    test_obj = viommu_base.VIOMMUTest(vm, test, params)
    if need_sriov:
        sroiv_test_obj = sriov_base.SRIOVTest(vm, test, params)

    try:
        test_obj.setup_iommu_test(iommu_dict=iommu_dict, cleanup_ifaces=cleanup_ifaces)
        test_obj.prepare_controller()
        test.log.info("TEST_STEP: Start the VM.")
        if not vm.is_alive():
            vm.start()
        vm_session = vm.wait_for_serial_login(
            timeout=int(params.get('login_timeout')),
            recreate_serial_console=True)
        pre_devices = viommu_base.get_devices_pci(vm_session, test_devices)
        if disk_dict:
            test.log.info("TEST_STEP: Attach a disk device to VM.")
            disk_dict = test_obj.update_disk_addr(disk_dict)
            disk_size = params.get("size", "200M")
            new_disk_path = os.path.join(data_dir.get_data_dir(), "images", "test.qcow2")
            libvirt_disk.create_disk("file", new_disk_path, disk_size, disk_format="qcow2")
            disk_dict.update({'source': {'attrs': {'file': new_disk_path}}})
            disk_obj = libvirt_vmxml.create_vm_device_by_type('disk', disk_dict)
            virsh.attach_device(vm.name, disk_obj.xml, debug=True, ignore_status=False, wait_for_event=True)

        if need_sriov:
            test_obj.params["iface_dict"] = str(sroiv_test_obj.parse_iface_dict())
        iface_dict = test_obj.parse_iface_dict()

        if cleanup_ifaces == "yes":
            iface_obj = libvirt_vmxml.create_vm_device_by_type("interface", iface_dict)
            virsh.attach_device(vm.name, iface_obj.xml, debug=True, ignore_status=False)
            # Sleep 20s to stabilize the attached interface
            time.sleep(20)

        test.log.info("TEST_STEP: Check dmesg message about iommu inside the vm.")
        vm_session.cmd("dmesg | grep -i 'Adding to iommu group'", timeout=300)
        dev_iommu_groups = viommu_base.check_vm_iommu_group(vm_session, test_devices, pre_devices)
        test.log.debug(f"Device iommu groups info: {dev_iommu_groups}")

        if "block" in test_devices:
            test.log.info("TEST_STEP: Check VM disk io.")
            new_disk = libvirt_disk.get_non_root_disk_name(vm_session)[0]
            utils_disk.dd_data_to_vm_disk(vm_session, new_disk)
            test.log.info("TEST_STEP: Detach the disk.")
            detach_dev("disk", dev_iommu_groups["block"], vm_session)

        test.log.info("TEST_STEP: Check VM network.")
        if need_sriov:
            sriov_check_points.check_vm_network_accessed(vm_session, ping_dest=ping_dest)
        else:

            s, o = utils_net.ping(ping_dest, count=5, timeout=240, session=vm_session)
            if s:
                test.fail("Failed to ping %s! status: %s, output: %s." % (ping_dest, s, o))

        test.log.info("TEST_STEP: Detach the interface.")
        detach_dev("interface", dev_iommu_groups.get("Eth"), vm_session)

    finally:
        test_obj.teardown_iommu_test()
        if new_disk_path and os.path.exists(new_disk_path):
            os.remove(new_disk_path)
