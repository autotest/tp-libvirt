import logging
import os
from autotest.client.shared import error
from virttest import virsh
from virttest import utils_libvirtd
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml import xcepts
from virttest.utils_test import libvirt
from provider import libvirt_version


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
        stat_ps = session.cmd_status("ps aux |grep [q]emu-ga | grep -v grep")
        if stat_ps != 0:
            session.cmd("service qemu-ga start")
            # Check if the qemu-ga really started
            stat_ps = session.cmd_status("ps aux |grep [q]emu-ga | grep -v grep")
            if stat_ps != 0:
                raise error.TestError("Fail to run qemu-ga in guest")

    # MAIN TEST CODE ###
    # Process cartesian parameters
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    vm_state = params.get("vm_state", "running")
    suspend_target = params.get("pm_suspend_target", "mem")
    pm_enabled = params.get("pm_enabled", "not_set")
    test_managedsave = "yes" == params.get("test_managedsave", "no")
    test_save_restore = "yes" == params.get("test_save_restore", "no")
    pmsuspend_error = 'yes' == params.get("pmsuspend_error", 'no')

    # Libvirt acl test related params
    uri = params.get("virsh_uri")
    unprivileged_user = params.get('unprivileged_user')
    if unprivileged_user:
        if unprivileged_user.count('EXAMPLE'):
            unprivileged_user = 'testacl'

    if not libvirt_version.version_compare(1, 1, 1):
        if params.get('setup_libvirt_polkit') == 'yes':
            raise error.TestNAError("API acl test not supported in current"
                                    + " libvirt version.")

    # A backup of original vm
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    vmxml_backup = vmxml.copy()

    # Expected possible fail patterns.
    # Error output should match one of these patterns.
    # An empty list mean test should succeed.
    fail_pat = []
    virsh_dargs = {'debug': True, 'ignore_status': True}
    if params.get('setup_libvirt_polkit') == 'yes':
        virsh_dargs_copy = virsh_dargs.copy()
        virsh_dargs_copy['uri'] = uri
        virsh_dargs_copy['unprivileged_user'] = unprivileged_user
        if pmsuspend_error:
            fail_pat.append('access denied')

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
            try:
                if vmxml.pm:
                    del vmxml.pm
            except xcepts.LibvirtXMLNotFoundError:
                pass
        else:
            pm_xml = vm_xml.VMPMXML()
            if suspend_target == 'mem':
                pm_xml.mem_enabled = pm_enabled
            elif suspend_target == 'disk':
                pm_xml.disk_enabled = pm_enabled
            elif suspend_target == 'hybrid':
                pm_xml.mem_enabled = pm_enabled
                pm_xml.disk_enabled = pm_enabled
            vmxml.pm = pm_xml
        vmxml.sync()

        vm_xml.VMXML.set_agent_channel(vm_name)
        vm.start()

        # Create swap partition/file if nessesary.
        need_mkswap = False
        if suspend_target in ['disk', 'hybrid']:
            need_mkswap = not vm.has_swap()
        if need_mkswap:
            logging.debug("Creating swap partition.")
            vm.create_swap_partition()

        try:
            libvirtd = utils_libvirtd.Libvirtd()
            savefile = os.path.join(test.tmpdir, "%s.save" % vm_name)
            session = vm.wait_for_login()
            check_vm_guestagent(session)
            # Touch a file on guest to test managed save command.
            if test_managedsave:
                session.cmd_status("touch pmtest")

            # Set vm state
            if vm_state == "paused":
                vm.pause()
            elif vm_state == "shutoff":
                vm.destroy()

            # Run test case
            result = virsh.dompmsuspend(vm_name, suspend_target, debug=True,
                                        uri=uri,
                                        unprivileged_user=unprivileged_user)
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
            if test_managedsave:
                ret = virsh.managedsave(vm_name, **virsh_dargs)
                libvirt.check_exit_status(ret)
                # Dompmwakeup should return false here
                ret = virsh.dompmwakeup(vm_name, **virsh_dargs)
                libvirt.check_exit_status(ret, True)
                ret = virsh.start(vm_name)
                libvirt.check_exit_status(ret)
                if not vm.is_paused():
                    raise error.TestFail("Vm status is not paused before pm wakeup")
                if params.get('setup_libvirt_polkit') == 'yes':
                    ret = virsh.dompmwakeup(vm_name, **virsh_dargs_copy)
                else:
                    ret = virsh.dompmwakeup(vm_name, **virsh_dargs)
                libvirt.check_exit_status(ret)
                if not vm.is_paused():
                    raise error.TestFail("Vm status is not paused after pm wakeup")
                ret = virsh.resume(vm_name, **virsh_dargs)
                libvirt.check_exit_status(ret)
                sess = vm.wait_for_login()
                if sess.cmd_status("ls pmtest && rm -f pmtest"):
                    raise error.TestFail("Check managed save failed on guest")
                sess.close()
            if test_save_restore:
                # Run a series of operations to check libvirtd status.
                ret = virsh.dompmwakeup(vm_name, **virsh_dargs)
                libvirt.check_exit_status(ret)
                ret = virsh.save(vm_name, savefile, **virsh_dargs)
                libvirt.check_exit_status(ret)
                ret = virsh.restore(savefile, **virsh_dargs)
                libvirt.check_exit_status(ret)
                # run pmsuspend again
                ret = virsh.dompmsuspend(vm_name, suspend_target, **virsh_dargs)
                libvirt.check_exit_status(ret)
                # save and restore the guest again.
                ret = virsh.save(vm_name, savefile, **virsh_dargs)
                libvirt.check_exit_status(ret)
                ret = virsh.restore(savefile, **virsh_dargs)
                libvirt.check_exit_status(ret)
                ret = virsh.destroy(vm_name, **virsh_dargs)
                libvirt.check_exit_status(ret)
                if not libvirtd.is_running():
                    raise error.TestFail("libvirtd crashed")

        finally:
            libvirtd.restart()
            # Remove the tmp file
            if os.path.exists(savefile):
                os.remove(savefile)
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

    finally:
        # Destroy the vm.
        if vm.is_alive():
            vm.destroy()
        # Recover xml of vm.
        vmxml_backup.sync()
