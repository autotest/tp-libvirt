import logging

from virttest import virsh
from virttest import utils_package
from virttest import libvirt_version


def run_cmd_in_guest(vm, cmd, test, timeout=60):
    """
    Run command in the guest
    :params vm: vm object
    :params cmd: a command needs to be ran
    """
    session = vm.wait_for_login()
    status, output = session.cmd_status_output(cmd, timeout=timeout)
    logging.debug("The '%s' output: %s", cmd, output)
    if status:
        session.close()
        test.error("Can not run '%s' in guest: %s" % (cmd, output))
    else:
        session.close()
        return output


def update_boot_option_and_reboot(vm, kernel_params, test):
    """
    Update boot option in the guest and then reboot guest
    :params vm: vm object
    :params cmd: a command needs to be ran
    :params test: test object
    """
    try:
        session = vm.wait_for_login()
        if not utils_package.package_install("grubby", session=session):
            test.error("Failed to install grubby package.")
        cmd = "grubby --update-kernel=`grubby --default-kernel` --args='%s'" % kernel_params
        logging.info(cmd)
        status, output = session.cmd_status_output(cmd)
        if status != 0:
            logging.error(output)
            test.error("Failed to modify guest kernel option.")
        logging.info("Rebooting guest ...")
        vm.reboot()
    except Exception as details:
        test.fail("Unable to get VM session: %s" % details)
    finally:
        if session:
            session.close()


def run(test, params, env):
    """
    1. Configure kernel cmdline to support kdump
    2. Start kdump service
    3. Inject NMI to the guest
    4. Check NMI times
    """
    for cmd in 'inject-nmi', 'qemu-monitor-command':
        if not virsh.has_help_command(cmd):
            test.cancel("This version of libvirt does not "
                        " support the %s test", cmd)

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    start_vm = params.get("start_vm")
    expected_nmi_times = params.get("expected_nmi_times", '0')
    kernel_params = params.get("kernel_params", "")
    unprivileged_user = params.get('unprivileged_user')
    if unprivileged_user:
        if unprivileged_user.count('EXAMPLE'):
            unprivileged_user = 'testacl'

    if not libvirt_version.version_compare(1, 1, 1):
        if params.get('setup_libvirt_polkit') == 'yes':
            test.cancel("API acl test not supported in current"
                        " libvirt version.")
    try:
        if kernel_params:
            update_boot_option_and_reboot(vm, kernel_params, test)
        if start_vm == "yes":
            # start kdump service in the guest
            cmd = "which kdump"
            try:
                run_cmd_in_guest(vm, cmd, test)
            except Exception:
                try:
                    # try to install kexec-tools on fedoraX/rhelx.y guest
                    run_cmd_in_guest(vm, "yum install -y kexec-tools", test)
                except Exception:
                    test.error("Requires kexec-tools(or the equivalent for your distro)")

            # enable kdump service in the guest
            cmd = "service kdump start"
            run_cmd_in_guest(vm, cmd, test, timeout=120)

            # filter original 'NMI' information from the /proc/interrupts
            cmd = "grep NMI /proc/interrupts"
            nmi_str = run_cmd_in_guest(vm, cmd, test)

            # filter CPU from the /proc/cpuinfo and count number
            cmd = "grep -E '^process' /proc/cpuinfo | wc -l"
            vcpu_num = run_cmd_in_guest(vm, cmd, test).strip()

            logging.info("Inject NMI to the guest via virsh inject_nmi")
            virsh.inject_nmi(vm_name, debug=True, ignore_status=False)

            logging.info("Inject NMI to the guest via virsh qemu_monitor_command")
            virsh.qemu_monitor_command(vm_name, '{"execute":"inject-nmi"}')

            # injects a Non-Maskable Interrupt into the default CPU (x86/s390)
            # or all CPUs (ppc64), as usual, the default CPU index is 0
            cmd = "grep NMI /proc/interrupts | awk '{print $2}'"
            nmi_from_default_vcpu = run_cmd_in_guest(vm, cmd, test)
            real_nmi_times = nmi_from_default_vcpu.splitlines()[0]
            logging.debug("The current Non-Maskable Interrupts: %s", real_nmi_times)

            # check Non-maskable interrupts times
            if real_nmi_times != expected_nmi_times:
                test.fail("NMI times aren't expected %s:%s"
                          % (real_nmi_times, expected_nmi_times))
    finally:
        if kernel_params:
            cmd = "grubby --update-kernel=`grubby --default-kernel` --remove-args='%s'" % kernel_params
            run_cmd_in_guest(vm, cmd, test)
            vm.reboot()
