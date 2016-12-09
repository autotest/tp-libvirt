import logging

from avocado.core import exceptions

from virttest import virsh


def run(test, params, env):
    """
    Test command: virsh resume.

    1) Start vm, Prepare options such as id, uuid
    2) Prepare vm state for test, default is paused.
    3) Prepare other environment
    4) Run command, get result.
    5) Check result.
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vm.verify_alive()

    # Get parameters
    vm_ref = params.get("resume_vm_ref", "domname")
    vm_state = params.get("resume_vm_state", "paused")
    option_suffix = params.get("resume_option_suffix")
    status_error = params.get("status_error", "no")
    readonly = params.get("readonly", "no") == 'yes'

    domid = vm.get_id()
    domuuid = vm.get_uuid()

    # Prepare vm state
    if vm_state == "paused":
        logging.info("Pausing VM %s", vm_name)
        vm.pause()
    elif vm_state == "shutoff":
        logging.info("Shutting off VM %s", vm_name)
        vm.destroy()

    # Prepare options
    if vm_ref == "domname":
        vm_ref = vm_name
    elif vm_ref == "domid":
        vm_ref = domid
    elif vm_ref == "domuuid":
        vm_ref = domuuid
    elif domid and vm_ref == "hex_id":
        if domid == "-":
            vm_ref = domid
        else:
            vm_ref = hex(int(domid))

    if option_suffix:
        vm_ref = "%s %s" % (vm_ref, option_suffix)

    try:
        # Run resume command
        result = virsh.resume(vm_ref, readonly=readonly, ignore_status=True,
                              debug=True)

        # Get vm state after virsh resume executed.
        domstate = vm.state()

        # Check status_error
        if status_error == "yes":
            if result.exit_status == 0:
                raise exceptions.TestFail(
                    "Expect to fail to resume but succeeded")
        elif status_error == "no":
            if domstate == "paused":
                raise exceptions.TestFail(
                    "Resume VM failed. State is still paused")
            if result.exit_status != 0:
                raise exceptions.TestFail(
                    "Expect to resume successfully but failed")
    finally:
        vm.destroy()
