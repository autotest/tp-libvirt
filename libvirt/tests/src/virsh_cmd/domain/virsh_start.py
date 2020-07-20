import logging

from virttest import remote
from virttest import virsh
from virttest import libvirt_xml
from virttest import utils_libvirtd
from virttest import ssh_key
from virttest import libvirt_version


def run(test, params, env):
    """
    Test command: virsh start.

    1) Get the params from params.
    2) Prepare libvirtd's status.
    3) Do the start operation.
    4) Result check.
    5) clean up.
    """
    # get the params from params
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm_ref = params.get("vm_ref", "vm1")
    opt = params.get("vs_opt", "")

    # Backup for recovery.
    vmxml_backup = libvirt_xml.vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    backup_name = vm_ref
    vm = None
    if vm_ref is not "":
        vm = env.get_vm(vm_ref)
    vmxml = libvirt_xml.VMXML()

    libvirtd_state = params.get("libvirtd", "on")
    pre_operation = params.get("vs_pre_operation", "")
    status_error = params.get("status_error", "no")

    try:
        # prepare before start vm
        if libvirtd_state == "on":
            utils_libvirtd.libvirtd_start()
        elif libvirtd_state == "off":
            utils_libvirtd.libvirtd_stop()

        if pre_operation == "rename":
            new_vm_name = params.get("vs_new_vm_name", "virsh_start_vm1")
            vm = libvirt_xml.VMXML.vm_rename(vm, new_vm_name)
            vm_ref = new_vm_name
        elif pre_operation == "undefine":
            vmxml = vmxml.new_from_dumpxml(vm_ref)
            vmxml.undefine()

        # do the start operation
        try:
            if pre_operation == "remote":
                # get the params for remote test
                remote_ip = params.get("remote_ip", "ENTER.YOUR.REMOTE.IP")
                remote_user = params.get("remote_user", "root")
                remote_pwd = params.get("remote_pwd", "ENTER.YOUR.REMOTE.PASSWORD")
                if pre_operation == "remote" and remote_ip.count("ENTER.YOUR."):
                    test.cancel("Remote test parameters not configured")

                ssh_key.setup_ssh_key(remote_ip, remote_user, remote_pwd)
                remote_uri = "qemu+ssh://%s/system" % remote_ip
                cmd_result = virsh.start(vm_ref, ignore_status=True, debug=True,
                                         uri=remote_uri)
                if cmd_result.exit_status:
                    test.fail("Start vm failed.\n Detail: %s" % cmd_result)
            elif opt.count("console"):
                # With --console, start command will print the
                # dmesg of guest in starting and turn into the
                # login prompt. In this case, we start it with
                # --console and login vm in console by
                # remote.handle_prompts().
                cmd = "start %s --console" % vm_ref
                virsh_session = virsh.VirshSession(virsh_exec=virsh.VIRSH_EXEC, auto_close=True)
                virsh_session.sendline(cmd)
                remote.handle_prompts(virsh_session, params.get("username", ""),
                                      params.get("password", ""), r"[\#\$]\s*$",
                                      timeout=60, debug=True)
            elif opt.count("autodestroy"):
                # With --autodestroy, vm will be destroyed when
                # virsh session closed. Then we execute start
                # command in a virsh session and start vm with
                # --autodestroy. Then we closed the virsh session,
                # and check the vm is destroyed or not.
                virsh_session = virsh.VirshSession(virsh_exec=virsh.VIRSH_EXEC, auto_close=True)
                cmd = "start %s --autodestroy" % vm_ref
                status = virsh_session.cmd_status(cmd)
                if status:
                    test.fail("Failed to start vm with --autodestroy.")
                # Close the session, then the vm shoud be destroyed.
                virsh_session.close()
            elif opt.count("force-boot"):
                # With --force-boot, VM will be stared from boot
                # even we have saved it with virsh managedsave.
                # In this case, we start vm and execute sleep 1000&,
                # then save it with virsh managedsave. At last, we
                # start vm with --force-boot. To verify the result,
                # we check the sleep process. If the process exists,
                # force-boot failed, else case pass.
                vm.start()
                session = vm.wait_for_login()
                status = session.cmd_status("sleep 1000&")
                if status:
                    test.error("Can not execute command in guest.")
                sleep_pid = session.cmd_output("echo $!").strip()
                virsh.managedsave(vm_ref)
                virsh.start(vm_ref, options=opt)
            else:
                cmd_result = virsh.start(vm_ref, options=opt)
                if cmd_result.exit_status:
                    if status_error == "no":
                        test.fail("Start vm failed.\n Detail: %s"
                                  % cmd_result)
                else:
                    # start vm successfully
                    if status_error == "yes":
                        if libvirtd_state == "off" and libvirt_version.version_compare(5, 6, 0):
                            logging.info("From libvirt version 5.6.0 libvirtd is restarted,"
                                         " command should succeed.")
                        else:
                            test.fail("Run successfully with wrong "
                                      "command!\n Detail:%s"
                                      % cmd_result)

            if opt.count("paused"):
                if not (vm.state() == "paused"):
                    test.fail("VM is not paused when started with "
                              "--paused.")
            elif opt.count("autodestroy"):
                if vm.is_alive():
                    test.fail("VM was started with --autodestroy,"
                              "but not destroyed when virsh session "
                              "closed.")
            elif opt.count("force-boot"):
                session = vm.wait_for_login()
                status = session.cmd_status("ps %s |grep '[s]leep 1000'"
                                            % sleep_pid)
                if not status:
                    test.fail("VM was started with --force-boot,"
                              "but it is restored from a"
                              " managedsave.")
            else:
                if status_error == "no" and not vm.is_alive() and pre_operation != "remote":
                    test.fail("VM was started but it is not alive.")

        except remote.LoginError as detail:
            test.fail("Failed to login guest.")
    finally:
        # clean up
        if libvirtd_state == "off":
            utils_libvirtd.libvirtd_start()

        elif pre_operation == "rename":
            libvirt_xml.VMXML.vm_rename(vm, backup_name)
        elif pre_operation == "remote":
            virsh.destroy(vm_ref, ignore_status=False, debug=True, uri=remote_uri)

        if vm and vm.is_paused():
            vm.resume()

        # Restore VM
        vmxml_backup.sync()
