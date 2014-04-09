import os
from autotest.client.shared import error
from virttest import virsh
from virttest import utils_libvirtd


def run(test, params, env):
    """
    Test command: virsh save.

    The command can save the RAM state of a running domain.
    1.Prepare test environment.
    2.When the libvirtd == "off", stop the libvirtd service.
    3.Run virsh save command with assigned options.
    4.Recover test environment.(If the libvirtd service is stopped ,start
      the libvirtd service.)
    5.Confirm the test result.

    """
    savefile = params.get("save_file", "save.file")
    if savefile:
        savefile = os.path.join(test.tmpdir, savefile)
    libvirtd = params.get("libvirtd", "on")
    extra_param = params.get("save_extra_param")
    vm_ref = params.get("save_vm_ref")
    progress = ("yes" == params.get("save_progress", "no"))
    options = params.get("save_option", "")
    status_error = ("yes" == params.get("save_status_error", "yes"))
    vm_name = params.get("main_vm", "virt-tests-vm1")
    vm = env.get_vm(vm_name)

    domid = vm.get_id()
    domuuid = vm.get_uuid()

    # set the option
    if vm_ref == "id":
        vm_ref = domid
    elif vm_ref == "hex_id":
        vm_ref = hex(int(domid))
    elif vm_ref == "uuid":
        vm_ref = domuuid
    elif vm_ref.count("invalid"):
        vm_ref = params.get(vm_ref)
    elif vm_ref.count("name"):
        vm_ref = vm_name
    vm_ref += (" %s" % extra_param)

    if libvirtd == "off":
        utils_libvirtd.libvirtd_stop()

    if progress:
        options += " --verbose"
    result = virsh.save(vm_ref, savefile, options, ignore_status=True)
    status = result.exit_status
    err_msg = result.stderr.strip()

    # recover libvirtd service start
    if libvirtd == "off":
        utils_libvirtd.libvirtd_start()

    if savefile:
        virsh.restore(savefile)

    # check status_error
    try:
        if status_error:
            if not status:
                raise error.TestFail("virsh run succeeded with an "
                                     "incorrect command")
        else:
            if status:
                raise error.TestFail("virsh run failed with a "
                                     "correct command")
            if progress and not err_msg.count("Save:"):
                raise error.TestFail("No progress information outputed!")
            if options.count("running"):
                if vm.is_dead() or vm.is_paused():
                    raise error.TestFail("Guest state should be"
                                         " running after restore"
                                         " due to the option --running")
            elif options.count("paused"):
                if not vm.is_paused():
                    raise error.TestFail("Guest state should be"
                                         " paused after restore"
                                         " due to the option --paused")
            else:
                if vm.is_dead():
                    raise error.TestFail("Guest state should be"
                                         " alive after restore"
                                         " since no option was specified")
    finally:
        if vm.is_paused():
            virsh.resume(vm_name)
