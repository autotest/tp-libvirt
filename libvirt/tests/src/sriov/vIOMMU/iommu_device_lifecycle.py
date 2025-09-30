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
from provider.vm_lifecycle import lifecycle_base

VIRSH_ARGS = {'debug': True, 'ignore_status': False}


def check_network_access(test, session, need_sriov, ping_dest):
    """
    Verify network connectivity in the VM after lifecycle operations.

    :param test: Test object for logging and assertions
    :param session: Active session to the VM
    :param need_sriov: Boolean indicating if SRIOV network testing is required
    :param ping_dest: Destination IP address to ping for connectivity test
    :raises: TestFail if network connectivity fails
    """
    if need_sriov:
        sriov_check_points.check_vm_network_accessed(session, ping_dest=ping_dest)
    else:
        s, o = utils_net.ping(ping_dest, count=5, timeout=10, session=session)
        if s:
            test.fail(f"Failed to ping {ping_dest}! status: {s}, output: {o}")


def test_save_suspend(test, vm, save_path, test_scenario):
    """
    Execute VM save/restore or suspend/resume lifecycle operations.

    :param test: Test object for logging and assertions
    :param vm: VM object to perform operations on
    :param save_path: File path where VM state will be saved
    :param test_scenario: Type of operation ('save_restore' or 'suspend_resume')
    :raises: TestFail if save/restore or suspend/resume operations fail
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


def test_reboot_reset_shutdown(test, vm, params, need_sriov, ping_dest, test_scenario):
    """
    Execute VM reboot, reset, or shutdown lifecycle operations with network verification.

    :param test: Test object for logging and assertions
    :param vm: VM object to perform operations on
    :param params: Test parameters dictionary containing timeout and loop settings
    :param need_sriov: Boolean indicating if SRIOV network testing is required
    :param ping_dest: Destination IP address for network connectivity verification
    :param test_scenario: Type of operation ('shutdown', 'reset', or 'reboot_many_times')
    :raises: TestFail if lifecycle operations or network connectivity fail
    """
    login_timeout = int(params.get('login_timeout', 240))

    vm.cleanup_serial_console()
    vm.create_serial_console()

    if test_scenario == "shutdown":
        lifecycle_base.test_shutdown(test, vm, params)
        if params.get("start_after_shutdown", "no") == "yes":
            test.log.info("TEST_STEP: Starting VM after shutdown")
            vm.start()
        else:
            return
    elif test_scenario == "reset":
        lifecycle_base.test_reset(test, vm, login_timeout)
    else:  # reboot_many_times
        for _ in range(int(params.get('loop_time', '5'))):
            lifecycle_base.test_reboot(test, vm, params, login_timeout)

    session = vm.wait_for_serial_login(timeout=login_timeout)
    check_network_access(test, session, need_sriov, ping_dest)
    session.close()


def setup_vm_xml(test, vm, test_obj, need_sriov=False, cleanup_ifaces=True):
    """
    Configure VM XML with IOMMU, device, and network interface settings.

    :param test: Test object for logging and assertions
    :param vm: VM object to configure
    :param test_obj: VIOMMU test object containing configuration parameters
    :param need_sriov: Boolean indicating if SRIOV network interfaces are required
    :param cleanup_ifaces: Boolean indicating if existing interfaces should be cleaned up
    :raises: TestFail if VM XML configuration fails
    """
    test.log.info("TEST_SETUP: Update VM XML.")
    test_obj.setup_iommu_test(iommu_dict=eval(test_obj.params.get('iommu_dict', '{}')),
                              cleanup_ifaces=cleanup_ifaces)
    test_obj.prepare_controller()

    for dev in ["disk", "video"]:
        dev_dict = eval(test_obj.params.get('%s_dict' % dev, '{}'))
        if dev == "disk":
            dev_dict = test_obj.update_disk_addr(dev_dict)
        libvirt_vmxml.modify_vm_device(
            vm_xml.VMXML.new_from_dumpxml(vm.name), dev, dev_dict)

    if need_sriov:
        sriov_test_obj = sriov_base.SRIOVTest(vm, test, test_obj.params)
        iface_dicts = sriov_test_obj.parse_iface_dict()
        test.log.debug(f"Provided SRIOV interface dictionaries: {iface_dicts}")
        test_obj.params["iface_dict"] = str(iface_dicts)

    iface_dict = test_obj.parse_iface_dict()
    if cleanup_ifaces:
        libvirt_vmxml.modify_vm_device(
                vm_xml.VMXML.new_from_dumpxml(vm.name),
                "interface", iface_dict)


def run(test, params, env):
    """
    Main test function for VM lifecycle operations with vIOMMU and various device configurations.

    Tests VM lifecycle scenarios (shutdown, reset, reboot, save/restore, suspend/resume)
    with vIOMMU enabled and different device types. Supports SRIOV network testing
    and validates network connectivity after each lifecycle operation.

    :param test: Test object for logging and assertions
    :param params: Test parameters dictionary containing scenario and configuration settings
    :param env: Test environment containing VM and host information
    :raises: TestFail if any lifecycle operation or network verification fails
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
        setup_vm_xml(test, vm, test_obj, need_sriov, cleanup_ifaces)

        test.log.info("TEST_STEP: Start the VM.")
        vm.start()
        test.log.debug(vm_xml.VMXML.new_from_dumpxml(vm.name))

        if test_scenario in ["reboot_many_times", "reset", "shutdown"]:
            test_reboot_reset_shutdown(test, vm, params, need_sriov, ping_dest, test_scenario)
        elif test_scenario in ["save_restore", "suspend_resume"]:
            test_save_suspend(test, vm, save_path, test_scenario)
        else:
            test.log.warning(f"Unknown scenario: {test_scenario}")

    finally:
        test_obj.teardown_iommu_test()
        if os.path.exists(save_path):
            os.remove(save_path)
