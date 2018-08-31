import logging
import os
import platform

from virttest import virt_vm
from virttest import data_dir
from virttest import virsh
from virttest import utils_libvirtd
from virttest import utils_misc
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml import xcepts
from virttest.utils_test import libvirt

from provider import libvirt_version


def run(test, params, env):
    """
    Test command: virsh dompmsuspend <domain> <target>
    The command suspends a running domain using guest OS's power management.
    """

    # MAIN TEST CODE ###
    # Process cartesian parameters
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    vm_state = params.get("vm_state", "running")
    suspend_target = params.get("pm_suspend_target", "mem")
    pm_enabled = params.get("pm_enabled", "not_set")
    pm_enabled_disk = params.get("pm_enabled_disk", "no")
    pm_enabled_mem = params.get("pm_enabled_mem", "no")
    test_managedsave = "yes" == params.get("test_managedsave", "no")
    test_save_restore = "yes" == params.get("test_save_restore", "no")
    test_suspend_resume = "yes" == params.get("test_suspend_resume", "no")
    pmsuspend_error = 'yes' == params.get("pmsuspend_error", 'no')
    pmsuspend_error_msg = params.get("pmsuspend_error_msg")
    agent_error_test = 'yes' == params.get("agent_error_test", 'no')
    arch = platform.processor()
    duration_value = int(params.get("duration", "0"))

    # Libvirt acl test related params
    uri = params.get("virsh_uri")
    unprivileged_user = params.get('unprivileged_user')
    if unprivileged_user:
        if unprivileged_user.count('EXAMPLE'):
            unprivileged_user = 'testacl'

    if not libvirt_version.version_compare(1, 1, 1):
        if params.get('setup_libvirt_polkit') == 'yes':
            test.cancel("API acl test not supported in current"
                        " libvirt version.")

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

    # Setup possible failure patterns excluding ppc
    if "ppc64" not in arch:
        if pm_enabled == 'not_set':
            fail_pat.append('not supported')
        if pm_enabled == 'no':
            fail_pat.append('disabled')

    if vm_state == 'paused':
        # For older version
        fail_pat.append('not responding')
        # For newer version
        fail_pat.append('not running')
    elif vm_state == 'shutoff':
        fail_pat.append('not running')
    if agent_error_test:
        fail_pat.append('not running')
        fail_pat.append('agent not available')
    if pmsuspend_error_msg:
        fail_pat.append(pmsuspend_error_msg)

    # RHEL6 or older releases
    unsupported_guest_err = 'suspend mode is not supported by the guest'

    try:
        if vm.is_alive():
            vm.destroy()

        # Set pm tag in domain's XML if needed.
        if "ppc64" not in arch:
            if pm_enabled == 'not_set':
                try:
                    if vmxml.pm:
                        del vmxml.pm
                except xcepts.LibvirtXMLNotFoundError:
                    pass
            else:
                pm_xml = vm_xml.VMPMXML()
                pm_xml.mem_enabled = pm_enabled_mem
                pm_xml.disk_enabled = pm_enabled_disk
                vmxml.pm = pm_xml
            vmxml.sync()

        try:
            vm.prepare_guest_agent()
        except virt_vm.VMStartError as info:
            if "not supported" in str(info).lower():
                test.cancel(info)
            else:
                test.error(info)
        # Selinux should be enforcing
        vm.setenforce(1)

        # Create swap partition/file if nessesary.
        need_mkswap = False
        if suspend_target in ['disk', 'hybrid']:
            need_mkswap = not vm.has_swap()
        if need_mkswap:
            logging.debug("Creating swap partition.")
            vm.create_swap_partition()

        try:
            libvirtd = utils_libvirtd.Libvirtd()
            savefile = os.path.join(data_dir.get_tmp_dir(), "%s.save" % vm_name)
            session = vm.wait_for_login()
            # Touch a file on guest to test managed save command.
            if test_managedsave:
                session.cmd_status("touch pmtest")
            session.close()

            # Set vm state
            if vm_state == "paused":
                vm.pause()
            elif vm_state == "shutoff":
                vm.destroy()

            # Run test case
            result = virsh.dompmsuspend(vm_name, suspend_target, duration=duration_value, debug=True,
                                        uri=uri,
                                        unprivileged_user=unprivileged_user)
            if result.exit_status == 0:
                if fail_pat:
                    test.fail("Expected failed with %s, but run succeed:\n%s" %
                              (fail_pat, result.stdout))
            else:
                if unsupported_guest_err in result.stderr:
                    test.cancel("Unsupported suspend mode:\n%s" % result.stderr)
                if not fail_pat:
                    test.fail("Expected success, but run failed:\n%s" % result.stderr)
                #if not any_pattern_match(fail_pat, result.stderr):
                if not any(p in result.stderr for p in fail_pat):
                    test.fail("Expected failed with one of %s, but "
                              "failed with:\n%s" % (fail_pat, result.stderr))

            # If pmsuspend_error is True, just skip below checking, and return directly.
            if pmsuspend_error:
                return
            # check whether the state changes to pmsuspended
            if not utils_misc.wait_for(lambda: vm.state() == 'pmsuspended', 30):
                test.fail("VM failed to change its state, expected state: "
                          "pmsuspended, but actual state: %s" % vm.state())

            if agent_error_test:
                ret = virsh.dompmsuspend(vm_name, "mem", **virsh_dargs)
                libvirt.check_result(ret, fail_pat)
                ret = virsh.dompmsuspend(vm_name, "disk", **virsh_dargs)
                libvirt.check_result(ret, fail_pat)
                ret = virsh.domtime(vm_name, **virsh_dargs)
                libvirt.check_result(ret, fail_pat)
            if test_managedsave:
                ret = virsh.managedsave(vm_name, **virsh_dargs)
                libvirt.check_exit_status(ret)
                # Dompmwakeup should return false here
                ret = virsh.dompmwakeup(vm_name, **virsh_dargs)
                libvirt.check_exit_status(ret, True)
                ret = virsh.start(vm_name)
                libvirt.check_exit_status(ret)
                if not vm.is_paused():
                    test.fail("Vm status is not paused before pm wakeup")
                if params.get('setup_libvirt_polkit') == 'yes':
                    ret = virsh.dompmwakeup(vm_name, **virsh_dargs_copy)
                else:
                    ret = virsh.dompmwakeup(vm_name, **virsh_dargs)
                libvirt.check_exit_status(ret)
                if not vm.is_paused():
                    test.fail("Vm status is not paused after pm wakeup")
                ret = virsh.resume(vm_name, **virsh_dargs)
                libvirt.check_exit_status(ret)
                sess = vm.wait_for_login()
                if sess.cmd_status("ls pmtest && rm -f pmtest"):
                    test.fail("Check managed save failed on guest")
                sess.close()
            if test_save_restore:
                # Run a series of operations to check libvirtd status.
                ret = virsh.dompmwakeup(vm_name, **virsh_dargs)
                libvirt.check_exit_status(ret)
                # Wait for vm is started
                vm.wait_for_login()
                ret = virsh.save(vm_name, savefile, **virsh_dargs)
                libvirt.check_exit_status(ret)
                ret = virsh.restore(savefile, **virsh_dargs)
                libvirt.check_exit_status(ret)
                # Wait for vm is started
                vm.wait_for_login()
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
                    test.fail("libvirtd crashed")
            if test_suspend_resume:
                ret = virsh.suspend(vm_name)
                libvirt.check_exit_status(ret, expect_error=True)
                if vm.state() != 'pmsuspended':
                    test.fail("VM state should be pmsuspended")
                ret = virsh.resume(vm_name)
                libvirt.check_exit_status(ret, expect_error=True)
                if vm.state() != 'pmsuspended':
                    test.fail("VM state should be pmsuspended")
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

            if need_mkswap:
                vm.cleanup_swap()

    finally:
        # Destroy the vm.
        if vm.is_alive():
            vm.destroy()
        # Recover xml of vm.
        vmxml_backup.sync()
