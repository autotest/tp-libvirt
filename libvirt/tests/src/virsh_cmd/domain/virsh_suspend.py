from virttest import virsh
from virttest import libvirt_version
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Test command: virsh suspend.

    The command can suspend a domain.
    1.Prepare test environment.
    2.Perform virsh suspend operation.
    3.Confirm the test result.
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    domid = vm.get_id()
    domuuid = vm.get_uuid()

    vm_ref = params.get("suspend_vm_ref", "")
    extra = params.get("suspend_extra", "")
    status_error = params.get("status_error", "no")
    suspend_readonly = "yes" == params.get("suspend_readonly", "no")

    uri = params.get("virsh_uri")
    unprivileged_user = params.get('unprivileged_user')
    if unprivileged_user:
        if unprivileged_user.count('EXAMPLE'):
            unprivileged_user = 'testacl'

    no_err_msg = False
    if not libvirt_version.version_compare(1, 1, 1):
        # Suspend may fail without error message if domain name is ''
        # on old version libvirt
        if vm_ref == '':
            no_err_msg = True
        if params.get('setup_libvirt_polkit') == 'yes':
            test.cancel("API acl test not supported in current libvirt "
                        "version.")

    # Run test case
    if vm_ref == "id":
        vm_ref = domid
    elif vm_ref == "hex_id":
        vm_ref = hex(int(domid.strip()))
    elif vm_ref.find("invalid") != -1:
        vm_ref = params.get(vm_ref)
    elif vm_ref == "name":
        vm_ref = "%s %s" % (vm_name, extra)
    elif vm_ref == "uuid":
        vm_ref = domuuid

    result = virsh.suspend(vm_ref, ignore_status=True,
                           unprivileged_user=unprivileged_user,
                           uri=uri, debug=True)
    status = result.exit_status
    output = result.stdout.strip()
    err = result.stderr.strip()
    if status == 0 and not vm.is_paused():
        status = 1

    # Test the readonly mode
    if suspend_readonly:
        result = virsh.suspend(vm_ref, ignore_status=True, debug=True, readonly=True)
        libvirt.check_exit_status(result, expect_error=True)
        # This is for status_error check
        status = result.exit_status
        if status:
            err = 1

    # resume the VM
    if vm.is_paused():
        result = virsh.resume(vm_ref, ignore_status=True,
                              unprivileged_user=unprivileged_user,
                              uri=uri, debug=True)
        if result.exit_status or vm.is_paused():
            status = 1

    # Check result
    if status_error == "yes":
        if not err and not no_err_msg:
            test.fail("No error hint to user about bad command!")
        if status == 0:
            test.fail("Run successfully with wrong command!")
    elif status_error == "no":
        if status != 0 or output == "":
            test.fail("Run failed with right command")
    else:
        test.fail("The status_error must be 'yes' or 'no'!")
