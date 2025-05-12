import time
import threading
import logging as log

from virttest import utils_test
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from aexpect.exceptions import ShellProcessTerminatedError
from aexpect.exceptions import ShellTimeoutError


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
    crash_utility = params.get("crash_utility", "no") == "yes"
    stress_time = params.get("stress_time", "30")
    crash_dir = params.get("crash_dir", "/var/crash/")
    debug_dir = params.get("debug_dir", "/home/")
    dump_options = params.get("dump_options", "--memory-only --bypass-cache")
    trigger_crash_cmd = "echo c > /proc/sysrq-trigger"

    def check_kdump_service(vm):
        """
        Check if kdump.service is running
        Current supported Distros: rhel, fedora, ubuntu

        param vm: vm object
        returns: None
        """
        # Set command based on different distros
        logging.info("Checking for kdump.service in guest %s" % vm.name)
        session = vm.wait_for_login(timeout=240)
        distro_details = session.cmd("cat /etc/os-release").lower()
        check_kdump_cmd = ""
        if "fedora" in distro_details or "rhel" in distro_details:
            check_kdump_cmd = "kdumpctl status"
        elif "ubuntu" in distro_details:
            check_kdump_cmd = "kdump-config status"
        else:
            test.cancel("Guest distro not supported")

        # Check the kdump.service status
        check_kdump_status, check_kdump = session.cmd_status_output(check_kdump_cmd)
        if check_kdump_status:
            logging.debug("Kdump service status: %s" % check_kdump)
            test.error("Kdump service is not running in guest %s" % vm.name)
        logging.info("Kdump service is up and running:\n%s" % check_kdump)
        session.close()

    def get_vmcores(vm):
        """
        Get vm-cores present in the crash directory

        param vm: vm object
        returns: list of vm-cores
        """
        logging.info("Getting vmcores in the guest %s" % vm.name)
        session = vm.wait_for_login(timeout=100)
        get_vmcores_cmd = "ls " + crash_dir
        vmcores = session.cmd(get_vmcores_cmd).split()
        session.close()
        return vmcores

    def check_guest_status(vm):
        """
        Check guest domstate. Guest should be running at all times.

        param vm: vm object
        returns:
        1. 0 if guest is running
        2. 1 if guest is not running
        """
        logging.info("Checking domstate of guest %s" % vm.name)
        if vm.state() != "running":
            logging.debug("Domain is not running: %s" % vm.state())
            return 1
        return 0

    def trigger_crash(vm, session):
        """
        Trigger sysrq triggered crash in guest

        param vm: vm object
        param session: session object
        returns: None
        """
        logging.info("Triggering sysrq crash in guest %s" % vm.name)
        try:
            session.cmd(trigger_crash_cmd)
        except ShellProcessTerminatedError:
            time.sleep(120)
        session.close()

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

    def crash_utility_tool(vm, vmcore):
        """
        Check the working of crash utility tool to analyse the guest dump
        Current supported Distros: rhel, fedora, ubuntu

        param vm: vm object
        param vm-core: guest vm-core file
        returns: None
        """
        logging.info("Debugging %s vmcore using crash utility" % vm.name)
        session = vm.wait_for_login(timeout=100)
        debug_libraries = []
        vmcore_file = crash_dir + vmcore + "/vmcore"
        distro_details = session.cmd("cat /etc/os-release").lower()
        guest_kernel = session.cmd("uname -r").strip()

        # Get required debug libraries based on distros
        if "fedora" in distro_details or "rhel" in distro_details:
            debug_libraries = ["*kexec-tools*", "*elfutils*", "*crash*", "*kdump-utils*"]
            if not guest_upstream_kernel:
                debug_libraries.append("*kernel-debuginfo*")
        elif "ubuntu" in distro_details:
            debug_libraries = ["*linux-crashdump*", "*kdump-tools*", "*crash*", "*elfutils*"]
        else:
            test.cancel("Guest distro not supported")

        # Check if required debug libraries are installed
        not_installed_libs = set()
        for library in debug_libraries:
            get_library_cmd = "rpm -qa " + library
            output = session.cmd(get_library_cmd).split()
            if not output:
                not_installed_libs.add(library)
        if not_installed_libs:
            test.error("Debug libraries not installed in %s: %s" % (vm.name, not_installed_libs))
        session.close()

        # Get debug kernel location based on distros
        if "fedora" in distro_details or "rhel" in distro_details:
            vmlinux = "/usr/lib/debug/lib/modules/" + guest_kernel + "/vmlinux"
        elif "ubuntu" in distro_details:
            vmlinux = "/usr/lib/debug/boot/vmlinux-" + guest_kernel
        else:
            test.cancel("Guest distro not supported")
        if guest_upstream_kernel:
            vmlinux = params.get("upstream_kernel_vmlinux", vmlinux)

        # Run the crash utility tool
        session = vm.wait_for_login(timeout=100)
        crash_cmd = f"crash {vmlinux} {vmcore_file}"
        crash_log = ""
        logging.debug("Crash command: %s", crash_cmd)
        try:
            session.cmd(crash_cmd)
        except ShellTimeoutError:
            crash_log = session.get_output()
            session.close()

        logging.debug("Crash utility output: %s" % crash_log)
        if "PID" not in crash_log or "crash>" not in crash_log:
            test.fail("Failed to debug %s vmcore using crash utiility" % vm.name)

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
            if check_guest_status(vm):
                failed_vms.add(vm.name)
                virsh_dump_vms.add(vm)
        if failed_vms:
            virsh_dump(virsh_dump_vms)
            test.fail("Guest %s not running after running stress" % failed_vms)

    # Trigger crash in guests in parallel
    kdump_threads = []
    for vm in vms:
        kdump_threads.append(
            threading.Thread(target=trigger_crash, args=(vm, vm.wait_for_login(timeout=100)))
        )
    time.sleep(20)
    for kdump_thread in kdump_threads:
        kdump_thread.start()
    for kdump_thread in kdump_threads:
        kdump_thread.join()

    # Check guest status after crash
    for vm in vms:
        try:
            if check_guest_status(vm):
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
    for vm in vms:
        post_vmcores[vm.name] = get_vmcores(vm)
        logging.info("%s vmcores after crash: %s" % (vm.name, post_vmcores[vm.name]))

    # Check if vm-core got generated after crash in guests
    for vm in vms:
        if len(post_vmcores[vm.name]) <= len(pre_vmcores[vm.name]):
            failed_vms.add(vm.name)
    if failed_vms:
        test.fail("vmcore not generated in %s" % failed_vms)

    # Debug vm-core using crash utility tool
    if crash_utility:
        for vm in vms:
            for vmcore_file in pre_vmcores[vm.name]:
                post_vmcores[vm.name].remove(vmcore_file)
            crash_utility_tool(vm, post_vmcores[vm.name][-1])

    # Unload the stress app in guests
    if guest_stress:
        unload_guest_stress(vms)
