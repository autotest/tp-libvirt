from autotest.client.shared import error
from virttest import virsh
from virttest import utils_libvirtd


def run(test, params, env):
    """
    Test command: virsh managedsave.

    This command can save and destroy a
    running domain, so it can be restarted
    from the same state at a later time.
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    # define function
    def vm_recover_check(guest_name, option):
        """
        Check if the vm can be recovered correctly.

        :param guest_name : Checked vm's name.
        :param option : managedsave command option.
        """
        # This time vm not be shut down
        if vm.is_alive():
            raise error.TestFail("Guest should be inactive")
        virsh.start(guest_name)
        # This time vm should be in the list
        if vm.is_dead():
            raise error.TestFail("Guest should be active")
        if option:
            if option.count("running"):
                if vm.is_dead() or vm.is_paused():
                    raise error.TestFail("Guest state should be"
                                         " running after started"
                                         " because of '--running' option")
            elif option.count("paused"):
                if not vm.is_paused():
                    raise error.TestFail("Guest state should be"
                                         " paused after started"
                                         " because of '--paused' option")
        else:
            if params.get("paused_after_start_vm") == "yes":
                if not vm.is_paused():
                    raise error.TestFail("Guest state should be"
                                         " paused after started"
                                         " because of initia guest state")

    domid = vm.get_id()
    domuuid = vm.get_uuid()

    status_error = ("yes" == params.get("status_error", "no"))
    vm_ref = params.get("managedsave_vm_ref")
    libvirtd = params.get("libvirtd", "on")
    extra_param = params.get("managedsave_extra_param", "")
    progress = ("yes" == params.get("managedsave_progress", "no"))
    option = params.get("managedsave_option", "")
    if option:
        if not virsh.has_command_help_match('managedsave', option):
            # Older libvirt does not have this option
            raise error.TestNAError("Older libvirt does not"
                                    " handle arguments consistently")

    # run test case
    if vm_ref == "id":
        vm_ref = domid
    elif vm_ref == "uuid":
        vm_ref = domuuid
    elif vm_ref == "hex_id":
        vm_ref = hex(int(domid))
    elif vm_ref.count("invalid"):
        vm_ref = params.get(vm_ref)
    elif vm_ref == "name":
        vm_ref = vm_name

    # stop the libvirtd service
    if libvirtd == "off":
        utils_libvirtd.libvirtd_stop()

    # Ignore exception with "ignore_status=True"
    if progress:
        option += " --verbose"
    option += extra_param
    ret = virsh.managedsave(vm_ref, options=option, ignore_status=True)
    status = ret.exit_status
    # The progress information outputed in error message
    error_msg = ret.stderr.strip()

    # recover libvirtd service start
    if libvirtd == "off":
        utils_libvirtd.libvirtd_start()

    # check status_error
    try:
        if status_error:
            if not status:
                raise error.TestFail("Run successfully with wrong command!")
        else:
            if status:
                raise error.TestFail("Run failed with right command")
            if progress:
                if not error_msg.count("Managedsave:"):
                    raise error.TestFail("Got invalid progress output")
            vm_recover_check(vm_name, option)
    finally:
        if vm.is_paused():
            virsh.resume(vm_name)
