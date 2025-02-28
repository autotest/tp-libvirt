from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_memory, libvirt_vmxml
from virttest.utils_test import libvirt

from provider.sriov import sriov_base


def run(test, params, env):
    """
    Hotplug memory to vm with hostdev type device and check the memory lock limit.
    """
    def setup_test(vm_name, dev_type, iface_dict):
        """
        Prepare vm xml with device and memory setting then start guest.

        :params vm_name: the vm name
        :params dev_type: the device type, includes hostdev device and interface
        :params iface_dict: the dict of hostdev interface/device
        :params return: the tuple of vmxml and with_hostdev_device value
        """
        test.log.info("SETUP_STEP1: prepare vm xml with memory setting.")
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        vmxml.setup_attrs(**vm_attrs)
        vmxml.sync()
        with_hostdev_device = False
        if not without_hostdev:
            test.log.info("SETUP_STEP2: prepare vm xml with device.")
            sriov_test_obj.setup_default(dev_name=dev_name)
            iface_dev = sriov_test_obj.create_iface_dev(dev_type, iface_dict)
            libvirt.add_vm_device(vmxml, iface_dev)
            with_hostdev_device = True
        return vmxml, with_hostdev_device

    def check_memory_limit(vmxml, current_mem, with_hostdev_device=True, mem_changed=True):
        """
        Check the locked memory limit.

        :params vmxml: the vm xml
        :params current_mem: the expected current memory
        :params with_hostdev_device: boolean to use check if the vm has hostdev device
        :params mem_changed: boolean to use check if the MEMLOCK is changed
        """
        if int(current_mem) != int(vmxml.get_current_mem() * 1024):
            test.fail("The current memory is incorrect after hot-unplugged.")

        # With_hardlimit: MEMLOCK value is equal to the hard_limit value
        if mem_set == "with_hardlimit":
            expr_memlock = libvirt_memory.normalize_mem_size(
                vmxml.memtune.hard_limit,
                vmxml.memtune.hard_limit_unit)

        # Without_hardlimit:  MEMLOCK value is current memory + 1G
        if mem_set == "without_hardlimit":
            if without_hostdev and not with_hostdev_device:
                expr_memlock = one_G_bytes // 16
            if with_hostdev_device:
                if not mem_changed:
                    expr_memlock = current_mem + one_G_bytes * 2
                else:
                    expr_memlock = current_mem + one_G_bytes

        if not libvirt_memory.comp_memlock(expr_memlock):
            test.fail("Unable to get correct MEMLOCK!")

    def hotplug_mem_device(vm_name, mem_dict, current_mem):
        """
        Hotplug the memory device and check locked memory limit.

        :params vm_name: the vm name
        :params mem_dict: the dict of memory device
        :params current_mem: the expected current memory
        """
        mem_xml = libvirt_vmxml.create_vm_device_by_type("memory", mem_dict)
        virsh.attach_device(vm_name, mem_xml.xml, debug=True, ignore_status=False)
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        check_memory_limit(vmxml, current_mem)

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    dev_type = params.get("dev_type", "")
    dev_source = params.get("dev_source", "")
    mem_set = params.get("mem_set", "")
    mem_dict1 = eval(params.get("mem_dict1", "{}"))
    mem_dict2 = eval(params.get("mem_dict2", "{}"))
    without_hostdev = "yes" == params.get("without_hostdev", "no")

    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    backup_vmxml = vmxml.copy()
    num_memory = int(vmxml.get_current_mem() / 2)
    vm_attrs = eval(params.get("vm_attrs", "{}") % (num_memory, num_memory))

    sriov_test_obj = sriov_base.SRIOVTest(vm, test, params)
    if dev_type == "hostdev_device" and dev_source.startswith("pf"):
        dev_name = sriov_test_obj.pf_dev_name
    else:
        dev_name = sriov_test_obj.vf_dev_name
    iface_dict = sriov_test_obj.parse_iface_dict()

    one_G_bytes = 1073741824

    try:
        vmxml, with_hostdev_device = setup_test(vm_name, dev_type, iface_dict)
        test.log.info("TEST_STEP: Start the vm and check locked memory limit.")
        vm.start()
        test.log.debug("The current guest xml is:%s", virsh.dumpxml(vm_name).stdout_text)
        vm_session = vm.wait_for_serial_login(timeout=240).close
        default_mem = libvirt_memory.normalize_mem_size(
                       vmxml.get_current_mem(),
                       vmxml.get_current_mem_unit())
        check_memory_limit(vmxml, default_mem, with_hostdev_device)
        if without_hostdev:
            test.log.info("TEST_STEP: Hotplug iface device and check locked memory limit.")
            iface_dev = sriov_test_obj.create_iface_dev(dev_type, iface_dict)
            virsh.attach_device(vm_name, iface_dev.xml, debug=True, ignore_status=False)
            vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
            check_memory_limit(vmxml, default_mem, with_hostdev_device=True)
        test.log.info("TEST_STEP: Hotplug 1st memory device and check locked memory limit.")
        first_mem = default_mem + one_G_bytes
        hotplug_mem_device(vm_name, mem_dict1, first_mem)
        test.log.info("TEST_STEP: Hotplug 2nd memory device and check locked memory limit.")
        second_mem = first_mem + one_G_bytes
        hotplug_mem_device(vm_name, mem_dict2, second_mem)
        test.log.info("TEST_STEP: Hot-unplug 2nd memory device and check locked memory limit.")
        memxml = vm_xml.VMXML.new_from_dumpxml(vm_name).get_devices('memory')[-1]
        virsh.detach_device(vm_name, memxml.xml, ignore_status=False, debug=True,
                            wait_for_event=True)
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        check_memory_limit(vmxml, first_mem, mem_changed=False)
        if without_hostdev:
            test.log.info("TEST_STEP: Hot-unplug iface device and check locked memory limit.")
            virsh.detach_device(vm_name, iface_dev.xml, debug=True, ignore_status=False,
                                wait_for_event=True)
            vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
            check_memory_limit(vmxml, first_mem, with_hostdev_device=False)
    finally:
        sriov_test_obj.teardown_default(dev_name=dev_name)
        backup_vmxml.sync()
