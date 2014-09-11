import logging

from virttest import virsh
from provider import libvirt_version
from autotest.client.shared import error


def run_cmd_in_guest(vm, cmd):
    """
    Run command in the guest
    :params vm: vm object
    :params cmd: a command needs to be ran
    """
    session = vm.wait_for_login()
    status, output = session.cmd_status_output(cmd)
    logging.debug("The '%s' output: %s", cmd, output)
    if status:
        session.close()
        raise error.TestError("Can not run '%s' in guest: %s", cmd, output)
    else:
        session.close()
        return output


def run(test, params, env):
    """
    1. Configure kernel cmdline to support kdump
    2. Start kdump service
    3. Inject NMI to the guest
    4. Check NMI times
    """
    for cmd in 'inject-nmi', 'qemu-monitor-command':
        if not virsh.has_help_command(cmd):
            raise error.TestNAError("This version of libvirt does not "
                                    " support the %s test", cmd)

    vm_name = params.get("main_vm", "virt-tests-vm1")
    vm = env.get_vm(vm_name)
    start_vm = params.get("start_vm")
    expected_nmi_times = params.get("expected_nmi_times", '0')
    unprivileged_user = params.get('unprivileged_user')
    if unprivileged_user:
        if unprivileged_user.count('EXAMPLE'):
            unprivileged_user = 'testacl'

    if not libvirt_version.version_compare(1, 1, 1):
        if params.get('setup_libvirt_polkit') == 'yes':
            raise error.TestNAError("API acl test not supported in current"
                                    + " libvirt version.")

    if start_vm == "yes":
        # start kdump service in the guest
        cmd = "which kdump"
        try:
            run_cmd_in_guest(vm, cmd)
        except:
            try:
                # try to install kexec-tools on fedoraX/rhelx.y guest
                run_cmd_in_guest(vm, "yum install -y kexec-tools")
            except:
                raise error.TestNAError("Requires kexec-tools(or the "
                                        "equivalent for your distro)")

        # enable kdump service in the guest
        cmd = "service kdump start"
        run_cmd_in_guest(vm, cmd)

        # filter original 'NMI' information from the /proc/interrupts
        cmd = "grep NMI /proc/interrupts"
        nmi_str = run_cmd_in_guest(vm, cmd)

        # filter CPU from the /proc/cpuinfo and count number
        cmd = "grep -E '^process' /proc/cpuinfo | wc -l"
        vcpu_num = run_cmd_in_guest(vm, cmd).strip()

        logging.info("Inject NMI to the guest via virsh inject_nmi")
        virsh.inject_nmi(vm_name, debug=True, ignore_status=False)

        logging.info("Inject NMI to the guest via virsh qemu_monitor_command")
        virsh.qemu_monitor_command(vm_name, '{"execute":"inject-nmi"}')

        # injects a Non-Maskable Interrupt into the default CPU (x86/s390)
        # or all CPUs (ppc64), as usual, the default CPU index is 0
        cmd = "grep NMI /proc/interrupts | awk '{print $2}'"
        nmi_from_default_vcpu = run_cmd_in_guest(vm, cmd)
        real_nmi_times = nmi_from_default_vcpu.splitlines()[0]
        logging.debug("The current Non-Maskable Interrupts: %s", real_nmi_times)

        # check Non-maskable interrupts times
        if real_nmi_times != expected_nmi_times:
            raise error.TestFail("NMI times aren't expected %s:%s",
                                 real_nmi_times, expected_nmi_times)
