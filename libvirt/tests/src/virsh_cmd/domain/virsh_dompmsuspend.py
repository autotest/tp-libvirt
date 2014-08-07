import logging
from autotest.client.shared import error
from virttest import virsh
from virttest.libvirt_xml.vm_xml import VMXML, VMPM


def run(test, params, env):
    """
    Test command: virsh dompmsuspend <domain> <target>
    The command suspends a running domain using guest OS's power management.
    """

    def check_vm_guestagent(session):
        # Check if qemu-ga already started automatically
        cmd = "rpm -q qemu-guest-agent || yum install -y qemu-guest-agent"
        stat_install, output = session.cmd_status_output(cmd, 300)
        logging.debug(output)
        if stat_install != 0:
            raise error.TestError("Fail to install qemu-guest-agent, make"
                                  "sure that you have usable repo in guest")

        # Check if qemu-ga already started
        stat_ps = session.cmd_status("ps aux |grep [q]emu-ga")
        if stat_ps != 0:
            session.cmd("qemu-ga -d")
            # Check if the qemu-ga really started
            stat_ps = session.cmd_status("ps aux |grep [q]emu-ga")
            if stat_ps != 0:
                raise error.TestError("Fail to run qemu-ga in guest")

    # MAIN TEST CODE ###
    # Process cartesian parameters
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    vm_state = params.get("vm_state", "running")
    suspend_target = params.get("pm_suspend_target", "mem")
    pm_enabled = params.get("pm_enabled", "not_set")

    # A backup of original vm
    vm_xml = VMXML.new_from_inactive_dumpxml(vm_name)
    vm_xml_backup = vm_xml.copy()

    # Expected possible fail patterns.
    # Error output should match one of these patterns.
    # An empty list mean test should succeed.
    fail_pat = []

    # Setup possible failure patterns
    if pm_enabled == 'not_set':
        fail_pat.append('not supported')
    if pm_enabled == 'no':
        fail_pat.append('disabled')

    if vm_state == 'paused':
        fail_pat.append('not responding')
    elif vm_state == 'shutoff':
        fail_pat.append('not running')

    try:
        if vm.is_alive():
            vm.destroy()

        # Set pm tag in domain's XML if needed.
        if pm_enabled == 'not_set':
            if 'pm' in vm_xml:
                del vm_xml.pm
        else:
            pm_xml = VMPM()
            if suspend_target == 'mem':
                pm_xml.mem_enabled = pm_enabled
            elif suspend_target == 'disk':
                pm_xml.disk_enabled = pm_enabled
            elif suspend_target == 'hybrid':
                if 'hybrid_enabled' in dir(pm_xml):
                    pm_xml.hybrid_enabled = pm_enabled
                else:
                    raise error.TestNAError("PM suspend type 'hybrid' is not "
                                            "supported yet.")
            vm_xml.pm = pm_xml
            vm_xml.sync()

        VMXML.set_agent_channel(vm_name)
        vm.start()

        # Create swap partition/file if nessesary.
        need_mkswap = False
        if suspend_target in ['disk', 'hybrid']:
            need_mkswap = not vm.has_swap()
        if need_mkswap:
            logging.debug("Creating swap partition.")
            vm.create_swap_partition()

        session = vm.wait_for_login()
        try:
            check_vm_guestagent(session)

            # Set vm state
            if vm_state == "paused":
                vm.pause()
            elif vm_state == "shutoff":
                vm.destroy()

            # Run test case
            result = virsh.dompmsuspend(vm_name, suspend_target, debug=True)
        finally:
            # Restore VM state
            if vm_state == "paused":
                vm.resume()

            if suspend_target in ['mem', 'hybrid']:
                if vm.state() == "pmsuspended":
                    virsh.dompmwakeup(vm_name)
            else:
                if vm.state() == "in shutdown":
                    vm.wait_for_shutdown()
                if vm.is_dead():
                    vm.start()

            # Cleanup
            session.close()

            if need_mkswap:
                vm.cleanup_swap()

        if result.exit_status == 0:
            if fail_pat:
                raise error.TestFail("Expected failed with %s, but run succeed"
                                     ":\n%s" % (fail_pat, result))
        else:
            if not fail_pat:
                raise error.TestFail("Expected success, but run failed:\n%s"
                                     % result)
            #if not any_pattern_match(fail_pat, result.stderr):
            if not any(p in result.stderr for p in fail_pat):
                raise error.TestFail("Expected failed with one of %s, but "
                                     "failed with:\n%s" % (fail_pat, result))
    finally:
        # Recover xml of vm.
        vm_xml_backup.sync()
