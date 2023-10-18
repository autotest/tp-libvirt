import re

from virttest import libvirt_version
from virttest import utils_misc
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.memory import Memory
from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_version import VersionInterval

VIRSH_ARGS = {'debug': True, 'ignore_status': False}


def run(test, params, env):
    """
    Update memory device
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
        libvirt_vmxml.remove_vm_devices_by_type(vm, 'memory')
        vm_attrs = eval(params.get('vm_attrs', '{}'))
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        vmxml.setup_attrs(**vm_attrs)
        vmxml.sync()
        mem_device_attrs = eval(params.get('mem_device_attrs', '{}'))
        mem_device = Memory()
        mem_device.setup_attrs(**mem_device_attrs)

        virsh.attach_device(vm_name, mem_device.xml, flagstr='--config',
                            debug=True, ignore_status=False)
        test.log.debug("vmxml is {}".format(vm_xml.VMXML.
                                            new_from_dumpxml(vm_name)))

    def run_test_virtio_mem():
        """
        Update memory device for virtio-mem device
        """
        mem_device_attrs = eval(params.get('mem_device_attrs', '{}'))
        vm.start()
        vm_session = vm.wait_for_login()
        cmdRes = vm_session.cmd_output('free -m')
        vm_mem_before = int(re.findall(r'Mem:\s+(\d+)\s+\d+\s+', cmdRes)[-1])
        test.log.debug("VM's memory before updating requested size: %s",
                       vm_mem_before)

        test.log.info(
            "TEST_STEP1: Update requested size for virtio-mem device.")
        vmxml_cur = vm_xml.VMXML.new_from_dumpxml(vm_name)
        mem_dev = vmxml_cur.devices.by_device_tag("memory")[0]
        mem_dev_alias = mem_dev.fetch_attrs()['alias']['name']
        virsh_opts = params.get('virsh_opts') % mem_dev_alias
        virsh.update_memory_device(vm.name, options=virsh_opts,
                                   wait_for_event=True, **VIRSH_ARGS)

        test.log.info("TEST_STEP2: Check requested and current size changes.")
        mem_dev = vm_xml.VMXML.new_from_dumpxml(vm_name).devices.\
            by_device_tag("memory")[0]
        expr_requested_size = int(float(utils_misc.normalize_data_size(
            params.get("requested_size", '80Mib'), order_magnitude='K')))
        for check_item in ['requested_size', 'current_size']:
            test.log.debug("Expected size is {}, Actual size is {}".
                           format(expr_requested_size,
                                  getattr(mem_dev.target, check_item)))
            if getattr(mem_dev.target, check_item) != expr_requested_size:
                test.fail("Incorrect %s! It should be %s, but got %s."
                          % (check_item, expr_requested_size,
                             getattr(mem_dev.target, check_item)))

        test.log.info("TEST_STEP3: Check 'MEMORY_DEVICE_SIZE_CHANGE' in "
                      "libvirtd/virtqemud log")
        log_file = utils_misc.get_path(test.debugdir, "libvirtd.log")
        check_log_str = params.get(
            "check_log_str", "MEMORY_DEVICE_SIZE_CHANGE")
        libvirt.check_logfile(check_log_str, log_file)

        test.log.info("TEST STEP4: Check memory in the VM.")
        cmdRes = vm_session.cmd_output('free -m')
        vm_mem_after = int(re.findall(r'Mem:\s+(\d+)\s+\d+\s+', cmdRes)[-1])
        mem_request_decrease = (mem_device_attrs['target']['requested_size'] -
                                expr_requested_size)/1024
        vm_mem_decrease = vm_mem_before - vm_mem_after
        if mem_request_decrease != vm_mem_decrease:
            test.fail("VM mem change comparison failed! Expect %d, but got %d."
                      % (mem_request_decrease, vm_mem_decrease))

    def cleanup_test_virtio_mem():
        """
        Clean up environment
        """
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
