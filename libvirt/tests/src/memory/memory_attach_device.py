from virttest import libvirt_version
from virttest import utils_misc
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.memory import Memory
from virttest.staging import utils_memory
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_version import VersionInterval

VIRSH_ARGS = {'debug': True, 'ignore_status': False}
ORG_HP = utils_memory.get_num_huge_pages()


def run(test, params, env):
    """
    Test memory function
    """
    def check_environment(vm, params):
        """
        Check the test environment

        :param vm: VM object
        :param params: Dictionary with the test parameters
        """
        libvirt_version.is_libvirt_feature_supported(params)
        utils_misc.is_qemu_function_supported(params)

        guest_required_kernel = params.get('guest_required_kernel')
        if guest_required_kernel:
            if not vm.is_alive():
                vm.start()
            vm_session = vm.wait_for_login()
            vm_kerv = vm_session.cmd_output('uname -r').strip().split('-')[0]
            vm_session.close()
            if vm_kerv not in VersionInterval(guest_required_kernel):
                test.cancel("Got guest kernel version:%s, which is not in %s" %
                            (vm_kerv, guest_required_kernel))

        if params.get("start_vm", "no") == "no":
            vm.destroy()

    def setup_test_default():
        """
        Default setup for test cases
        """
        pass

    def cleanup_test_default():
        """
        Default cleanup for test cases
        """
        pass

    def setup_test_virtio_mem():
        """
        Setup vmxml for test
        """
        set_num_huge_pages = params.get("set_num_huge_pages")
        if set_num_huge_pages:
            utils_memory.set_num_huge_pages(int(set_num_huge_pages))

        libvirt_vmxml.remove_vm_devices_by_type(vm, 'memory')
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        vm_attrs = eval(params.get('vm_attrs', '{}'))
        vmxml.setup_attrs(**vm_attrs)
        vmxml.sync()

        if params.get("start_vm") == "yes":
            vm.start()
            vm.wait_for_login().close()

    def run_test_virtio_mem():
        """
        Attach a virtio-mem device
        """
        mem_device_attrs = eval(params.get('mem_device_attrs', '{}'))
        mem_device = Memory()
        mem_device.setup_attrs(**mem_device_attrs)

        test.log.info("TEST_STEP1: Attach a virtio-mem device.")
        options = '' if vm.is_alive() else '--config'
        virsh.attach_device(vm_name, mem_device.xml, flagstr=options,
                            debug=True, ignore_status=False)

        if not vm.is_alive():
            vm.start()
            vm.wait_for_login().close()

        test.log.info("TEST_STEP2: Check requested and current size.")
        vmxml_cur = vm_xml.VMXML.new_from_dumpxml(vm_name)
        test.log.debug("Current VM XML: %s.", vmxml_cur)
        mem_dev = vmxml_cur.devices.by_device_tag("memory")[0]
        expr_requested_size = mem_device_attrs['target']['requested_size']
        for check_item in ['requested_size', 'current_size']:
            if getattr(mem_dev.target, check_item) != expr_requested_size:
                test.fail("Incorrect %s! It should be %s, but got %s."
                          % (check_item, expr_requested_size,
                             getattr(mem_dev.target, check_item)))

        test.log.info("TEST_STEP3: Check VM memory.")
        memory_gap = vmxml_cur.get_memory() - vmxml_cur.get_current_mem()
        virtio_mem_gap = mem_dev.target.get_size() - \
            mem_dev.target.get_current_size()
        if memory_gap != virtio_mem_gap:
            test.fail("Size of memory - currentMemory(%s) should be equal to "
                      "virtio-mem size - current(%s)."
                      % (memory_gap, virtio_mem_gap))

    def cleanup_test_virtio_mem():
        """
        Clean up environment
        """
        if utils_memory.get_num_huge_pages() != ORG_HP:
            utils_memory.set_num_huge_pages(ORG_HP)

    # Variable assignment
    test_case = params.get('test_case', '')

    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)

    check_environment(vm, params)
    bkxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    # Get setup function
    setup_test = eval('setup_test_%s' % test_case) \
        if 'setup_test_%s' % test_case in locals() else setup_test_default
    # Get runtest function
    run_test = eval('run_test_%s' % test_case)
    # Get cleanup function
    cleanup_test = eval('cleanup_test_%s' % test_case) \
        if 'cleanup_test_%s' % test_case in locals() else cleanup_test_default

    try:
        # Execute test
        setup_test()
        run_test()

    finally:
        bkxml.sync()
        cleanup_test()
