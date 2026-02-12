from avocado.utils import cpu
from virttest import virsh
from virttest.libvirt_xml import vm_xml

from provider.viommu import viommu_base


def run(test, params, env):
    """
    Start vm with Intel iommu and dma_translation attribute.
    """
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    dma_translation = params.get("dma_translation")
    guest_vcpus = params.get("guest_vcpus")
    eim_dict = eval(params.get("eim_dict", "{}"))
    iommu_dict = eval(params.get("iommu_dict", "{}"))
    with_more_vcpus = "yes" == params.get("with_more_vcpus", "no")
    test_obj = viommu_base.VIOMMUTest(vm, test, params)

    try:
        iommu_dict['driver'].update(eim_dict)
        test.log.info("TEST STEP1: Prepare guest xml with intel iommu device.")
        test_obj.setup_iommu_test(iommu_dict=iommu_dict)
        if with_more_vcpus:
            vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
            guest_vcpus = cpu.online_count()
            vmxml.vcpu = int(guest_vcpus)
            vmxml.sync()
        test.log.info("TEST STEP2: Start the guest.")
        vm.start()
        test.log.debug("The current guest xml ls: %s",
                       virsh.dumpxml(vm_name).stdout_text)
        test.log.info("TEST STEP3: Check the message for iommu group and DMA.")
        vm_session = vm.wait_for_serial_login(timeout=1000, recreate_serial_console=True)
        dma_status, dma_o = vm_session.cmd_status_output("dmesg | grep -i 'Not attempting DMA translation'")
        iommu_status, iommu_o = vm_session.cmd_status_output("dmesg | grep -i 'Adding to iommu group'")
        if dma_translation == "on" and (not dma_status or iommu_status):
            test.fail("Can't get expected dma message %s when enabling dma_translation." % dma_o)
        if dma_translation == "off" and (dma_status or not iommu_status):
            test.fail("Can't get expected iommu message %s when disabling dma_translation." % iommu_o)
        if with_more_vcpus:
            s, o = vm_session.cmd_status_output("cat /proc/cpuinfo | grep processor| wc -l")
            if int(o) != int(guest_vcpus):
                test.fail("Can't get expected vCPU number, the actual number is %s" % o)
    finally:
        test_obj.teardown_iommu_test()
