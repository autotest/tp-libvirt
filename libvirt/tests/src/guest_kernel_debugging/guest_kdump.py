import time
import threading
import logging as log

from virttest import utils_kdump
from virttest import utils_test
from virttest import virsh
from virttest.libvirt_xml import vm_xml


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test: Kdump of guest

    param test:   kvm test object
    param params: Dictionary with the test parameters
    param env:    Dictionary with test environment.

    This script is used to test the kdump functionality of the guest(s).
    1. Check if kdump.service is operational
    2. Get the vm-cores present in the guest
    3. Load the stress app if specified
    4. Trigger crash simultaneously in all guests
    5. Debug vm-core using crash utility if specified
    """
    vms = env.get_all_vms()
    guest_stress = params.get("guest_stress", "no") == "yes"
    guest_upstream_kernel = params.get("guest_upstream_kernel", "no") == "yes"
    upstream_kernel_vmlinux = params.get("upstream_kernel_vmlinux")
    crash_utility = params.get("crash_utility", "no") == "yes"
    setup_kdump = params.get("setup_kdump", "yes") == "yes"
    install_missing_packages = params.get("install_missing_packages", "yes") == "yes"
    crashkernel_value = params.get("crashkernel_value", "1024M")
    stress_time = params.get("stress_time", "30")
    crash_dir = params.get("crash_dir", "/var/crash/")
    debug_dir = params.get("debug_dir", "/home/")
    dump_options = params.get("dump_options", "--memory-only --bypass-cache")
    enable_sysrq_cmd = "echo 1 > /proc/sys/kernel/sysrq"
    trigger_crash_cmd = "echo c > /proc/sysrq-trigger"

    def check_kdump_service(vm):
        """
        Check if kdump.service is running
        Current supported Distros: rhel, fedora, ubuntu, sles, suse

        param vm: vm object
        returns: None
        """
        session = vm.wait_for_login(timeout=240)
        distro_info = utils_kdump.get_distro_info(session)
        session.close()
        if distro_info["name"] == "unknown":
            test.cancel("Guest distro not supported")
        utils_kdump.check_kdump_service(
            vm,
            distro_info,
            crashkernel_value=crashkernel_value if setup_kdump else None,
            test=test,
        )

    def get_vmcores(vm):
        """
        Get vmcore file paths present in the crash directory.

        param vm: vm object
        returns: list of vmcore file paths
        """
        return utils_kdump.get_vmcores(vm, crash_dir=crash_dir, test=test)

    def load_guest_stress(vms):
        """
        Load stress app in all the vms

        param vms: all vm objects
        returns: None
        """
        logging.info("Starting stress app in guest(s)")
        try:
            utils_test.load_stress("stress_in_vms", params=params, vms=vms)
        except Exception as err:
            test.fail("Error running stress in vms: %s" % str(err))

    def unload_guest_stress(vms):
        """
        Unload stress app in all the vms

        param vms: all vm objects
        returns: None
        """
        logging.info("Stopping stress app in guest(s)")
        utils_test.unload_stress("stress_in_vms", params=params, vms=vms)

    def virsh_dump(failed_vms):
        """
        Take virsh dump of guest in case of guest failure

        param failed_vms: vm objects which are failed/broken
        returns: None
        """
        logging.info("Dumping failed vms to directory %s" % debug_dir)
        for vm in failed_vms:
            if vm.state() != "shut off":
                logging.debug("Dumping %s to debug_dir %s" % (vm.name, debug_dir))
                virsh.dump(vm.name, debug_dir+vm.name+"-core",
                           dump_options, ignore_status=False,
                           debug=True)
                logging.debug("Successfully dumped %s as %s-core" % (vm.name, vm.name))
            else:
                logging.debug("Cannot dump %s as it is in shut off state" % vm.name)

    def crash_utility_tool(vm, vmcore_file):
        """
        Check the working of crash utility tool to analyse the guest dump
        Current supported Distros: rhel, fedora, ubuntu, sles, suse

        param vm: vm object
        param vmcore_file: guest vmcore file path
        returns: None
        """
        session = vm.wait_for_login(timeout=240)
        distro_info = utils_kdump.get_distro_info(session)
        session.close()
        if distro_info["name"] == "unknown":
            test.cancel("Guest distro not supported")
        utils_kdump.analyze_vmcore_with_crash(
            vm,
            distro_info,
            vmcore_file,
            upstream_kernel=guest_upstream_kernel,
            vmlinux_path=upstream_kernel_vmlinux,
            test=test,
        )

    # Declaring variables before starting test
    failed_vms = set()
    virsh_dump_vms = set()

    # Set on_crash value to preserve in guests
    for vm in vms:
        logging.info("Setting on_crash to preserve in %s" % vm.name)
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm.name)
        if vm.is_alive():
            vm.destroy(gracefully=False)
        vmxml.on_crash = "restart"
        vmxml.sync()
        vm.start()

    # Setup kdump packages/configuration before validation
    if setup_kdump:
        for vm in vms:
            session = vm.wait_for_login(timeout=240)
            distro_info = utils_kdump.get_distro_info(session)
            session.close()
            if distro_info["name"] == "unknown":
                test.cancel("Guest distro not supported")
            packages_installed = utils_kdump.ensure_kdump_packages(
                vm,
                distro_info,
                install_missing=install_missing_packages,
                test=test,
            )
            if packages_installed:
                logging.info("Rebooting %s after kdump package installation", vm.name)
                vm.reboot(session=None, timeout=240)
            utils_kdump.configure_kdump(
                vm,
                distro_info,
                crashkernel_value=crashkernel_value,
                test=test,
            )

    # Check for kdump service if it is operational
    for vm in vms:
        check_kdump_service(vm)

    # Check for the present vm-cores in guests
    pre_vmcores = {}
    for vm in vms:
        pre_vmcores[vm.name] = get_vmcores(vm)
        logging.info("%s vmcores before crash: %s" % (vm.name, pre_vmcores[vm.name]))

    # Load the stress app
    if guest_stress:
        load_guest_stress(vms)
        logging.info("Started running stress app")
        logging.info("Sleeping for %s seconds" % stress_time)
        time.sleep(int(stress_time))

        for vm in vms:
            if utils_kdump.check_guest_status(vm):
                failed_vms.add(vm.name)
                virsh_dump_vms.add(vm)
        if failed_vms:
            virsh_dump(virsh_dump_vms)
            test.fail("Guest %s not running after running stress" % failed_vms)

    # Trigger crash in guests in parallel
    kdump_threads = []
    for vm in vms:
        kdump_threads.append(
            threading.Thread(
                target=utils_kdump.trigger_crash,
                args=(vm, None, enable_sysrq_cmd, trigger_crash_cmd, 120, test),
            )
        )
    time.sleep(20)
    for kdump_thread in kdump_threads:
        kdump_thread.start()
    for kdump_thread in kdump_threads:
        kdump_thread.join()

    # Check guest status after crash
    for vm in vms:
        try:
            if utils_kdump.check_guest_status(vm):
                raise Exception("Guest %s not running after triggering crash" % vm.name)
            session = vm.wait_for_login(timeout=240)
            logging.info("Able to login into %s" % vm.name)
            session.close()
        except Exception as err:
            logging.debug("Error occured %s" % str(err))
            failed_vms.add(vm.name)
            virsh_dump_vms.add(vm)
    if failed_vms:
        virsh_dump(virsh_dump_vms)
        test.fail("Unable to login into %s after triggering crash" % failed_vms)

    # Check for the vm-cores in guests after crash
    post_vmcores = {}
    new_vmcores = {}
    for vm in vms:
        post_vmcores[vm.name] = get_vmcores(vm)
        logging.info("%s vmcores after crash: %s" % (vm.name, post_vmcores[vm.name]))
        new_vmcores[vm.name] = sorted(
            list(set(post_vmcores[vm.name]) - set(pre_vmcores[vm.name]))
        )

    # Check if vm-core got generated after crash in guests
    for vm in vms:
        if not new_vmcores[vm.name]:
            failed_vms.add(vm.name)
    if failed_vms:
        test.fail("vmcore not generated in %s" % failed_vms)

    # Debug vm-core using crash utility tool
    if crash_utility:
        for vm in vms:
            session = vm.wait_for_login(timeout=240)
            distro_info = utils_kdump.get_distro_info(session)
            session.close()
            if distro_info["name"] == "unknown":
                test.cancel("Guest distro not supported")
            utils_kdump.ensure_crash_utility_packages(
                vm,
                distro_info,
                install_missing=install_missing_packages,
                upstream_kernel=guest_upstream_kernel,
                test=test,
            )
            crash_utility_tool(vm, new_vmcores[vm.name][-1])

    # Unload the stress app in guests
    if guest_stress:
        unload_guest_stress(vms)
