import os

from virttest import utils_misc
from virttest import utils_net
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml

from provider.save import save_base
from provider.sriov import sriov_base
from provider.viommu import viommu_base
from provider.sriov import check_points as sriov_check_points

VIRSH_ARGS = {'debug': True, 'ignore_status': False}


def check_network_access(test, vm, session, need_sriov, ping_dest):
    """
    Check network access in VM
    """
    if need_sriov:
        sriov_check_points.check_vm_network_accessed(session, ping_dest=ping_dest)
    else:
        s, o = utils_net.ping(ping_dest, count=5, timeout=10, session=session)
        if s:
            test.fail(f"Failed to ping {ping_dest}! status: {s}, output: {o}")


def test_shutdown(test, vm, params):
    """
    Test shutdown scenario

    :param test: Test object
    :param vm: VM object
    :param params: Dictionary with the test parameters
    :raises: TestFail if VM fails to shutdown
    """
    test.log.info("TEST_STEP: Shutdown the VM.")
    virsh.shutdown(vm.name, **VIRSH_ARGS)
    shutdown_timeout = int(params.get("shutdown_timeout", "60"))
    if not utils_misc.wait_for(lambda: vm.is_dead(), shutdown_timeout):
        test.fail("VM failed to shutdown")
    test.log.info("VM successfully shutdown")


def test_reset(test, vm, login_timeout):
    """
    Test reset scenario
    
    :param test: Test object
    :param vm: VM object
    :param login_timeout: Login timeout
    """
    test.log.info("TEST_STEP: Reset the VM.")
    session = vm.wait_for_serial_login(timeout=login_timeout)
    virsh.reset(vm.name, **VIRSH_ARGS)
    _match, _text = session.read_until_last_line_matches(
            [r"[Ll]ogin:\s*"], timeout=login_timeout, internal_timeout=0.5)
    session.close()


def test_reboot(test, vm, params, login_timeout):
    """
    Test single reboot operation
    
    :param test: Test object
    :param vm: VM object
    :param params: Dictionary with the test parameters
    """
    test.log.info("TEST_STEP: Reboot the VM.")
    session = vm.wait_for_serial_login(timeout=login_timeout)
    session.sendline(params.get("reboot_command"))
    _match, _text = session.read_until_last_line_matches(
        [r"[Ll]ogin:\s*"], timeout=login_timeout, internal_timeout=0.5)
    session.close()


def test_reboot_reset_shutdown(test, vm, params, need_sriov, ping_dest, test_scenario):
    """
    Test reboot or reset scenario
    """
    # Common parameters
    login_timeout = int(params.get('login_timeout', 240))
    
    # Setup serial console
    vm.cleanup_serial_console()
    vm.create_serial_console()
    
    if test_scenario == "shutdown":
        test_shutdown(test, vm, params, need_sriov, ping_dest)
        if params.get("start_after_shutdown", "no") == "yes":
            test.log.info("TEST_STEP: Starting VM after shutdown")
            vm.start()
        else:
            return
    elif test_scenario == "reset":
        test_reset(test, vm, login_timeout)
    else:  # reboot_many_times
        for _ in range(int(params.get('loop_time', '5'))):
            test_reset(test, vm, login_timeout)

    # Final login to check network access
    session = vm.wait_for_serial_login(timeout=login_timeout)
    check_network_access(test, vm, session, need_sriov, ping_dest)
    session.close()


def test_save_suspend(test, vm, params, save_path, test_scenario):
    """
    Test save/restore or suspend/resume scenario
    """
    pid_ping, upsince = save_base.pre_save_setup(vm, serial=True)
    
    if test_scenario == "save_restore":
        test.log.info("TEST_STEP: Save and restore the VM.")
        virsh.save(vm.name, save_path, **VIRSH_ARGS)
        virsh.restore(save_path, **VIRSH_ARGS)
    elif test_scenario == "suspend_resume":
        test.log.info("TEST_STEP: Suspend and resume the VM.")
        virsh.suspend(vm.name, **VIRSH_ARGS)
        virsh.resume(vm.name, **VIRSH_ARGS)
    
    save_base.post_save_check(vm, pid_ping, upsince, serial=True)


def setup_vm_xml(test, vm, test_obj, need_sriov=False, cleanup_ifaces=True):
    """
    Setup VM XML with required configurations
    """
    test.log.info("TEST_SETUP: Update VM XML.")
    # Setup IOMMU test environment
    test_obj.setup_iommu_test(iommu_dict=eval(test_obj.params.get('iommu_dict', '{}')),
                              cleanup_ifaces=cleanup_ifaces)
    test_obj.prepare_controller()
    
    # Configure devices
    for dev in ["disk", "video"]:
        dev_dict = eval(test_obj.params.get('%s_dict' % dev, '{}'))
        if dev == "disk":
            dev_dict = test_obj.update_disk_addr(dev_dict)
        libvirt_vmxml.modify_vm_device(
            vm_xml.VMXML.new_from_dumpxml(vm.name), dev, dev_dict)
    
    # Configure network interfaces
    if need_sriov:
        sriov_test_obj = sriov_base.SRIOVTest(vm, test, test_obj.params)
        iface_dicts = sriov_test_obj.parse_iface_dict()
        test.log.debug(iface_dicts)
        test_obj.params["iface_dict"] = str(sriov_test_obj.parse_iface_dict())
    
    iface_dict = test_obj.parse_iface_dict()
    if cleanup_ifaces:
        libvirt_vmxml.modify_vm_device(
                vm_xml.VMXML.new_from_dumpxml(vm.name),
                "interface", iface_dict)


def run(test, params, env):
    """
    Test lifecycle for vm with vIOMMU enabled with different devices.
    """
    cleanup_ifaces = "yes" == params.get("cleanup_ifaces", "yes")
    ping_dest = params.get('ping_dest', "127.0.0.1")
    test_scenario = params.get("test_scenario")
    need_sriov = "yes" == params.get("need_sriov", "no")

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)

    rand_id = utils_misc.generate_random_string(3)
    save_path = f'/var/tmp/{vm_name}_{rand_id}.save'
    test_obj = viommu_base.VIOMMUTest(vm, test, params)
    
    try:
        # Setup VM XML configuration
        setup_vm_xml(test, vm, test_obj, need_sriov, cleanup_ifaces)

        # Start the VM
        test.log.info("TEST_STEP: Start the VM.")
        vm.start()
        test.log.debug(vm_xml.VMXML.new_from_dumpxml(vm.name))
        
        # Execute the appropriate test scenario
        if test_scenario in ["reboot_many_times", "reset", "shutdown"]:
            test_reboot_reset_shutdown(test, vm, params, need_sriov, ping_dest, test_scenario)
        elif test_scenario in ["save_restore", "suspend_resume"]:
            test_save_suspend(test, vm, params, save_path, test_scenario)
        else:
            test.log.warning(f"Unknown scenario: {test_scenario}")
        
    finally:
        test_obj.teardown_iommu_test()
        if os.path.exists(save_path):
            os.remove(save_path)

