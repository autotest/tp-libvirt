from virttest import ssh_key
from virttest import virsh
from virttest import utils_libvirtd


def run(test, params, env):
    """
    Test command: virsh dominfo.

    The command returns basic information about the domain.
    1.Prepare test environment.
    2.When the libvirtd == "off", stop the libvirtd service.
    3.Perform virsh dominfo operation.
    4.Recover test environment.
    5.Confirm the test result.
    """

    vm_name = params.get("main_vm", "vm1")
    vm = env.get_vm(vm_name)
    if vm.is_alive() and params.get("start_vm") == "no":
        vm.destroy()

    domid = vm.get_id()
    domuuid = vm.get_uuid()

    vm_ref = params.get("dominfo_vm_ref")
    extra = params.get("dominfo_extra", "")
    status_error = params.get("status_error", "no")
    libvirtd = params.get("libvirtd", "on")
    remote_ip = params.get("remote_ip", "REMOTE.EXAMPLE.COM")
    remote_pwd = params.get("remote_pwd", "")
    remote_user = params.get("remote_user", "root")
    remote_uri = params.get("remote_uri")

    # run test case
    if vm_ref == "id":
        vm_ref = domid
    elif vm_ref == "hex_id":
        vm_ref = hex(int(domid))
    elif vm_ref.find("invalid") != -1:
        vm_ref = params.get(vm_ref)
    elif vm_ref == "name":
        vm_ref = "%s %s" % (vm_name, extra)
    elif vm_ref == "uuid":
        vm_ref = domuuid

    if libvirtd == "off":
        utils_libvirtd.libvirtd_stop()

    if remote_uri:
        if remote_ip.count("EXAMPLE.COM"):
            test.cancel("please configure remote_ip first.")
        ssh_key.setup_ssh_key(remote_ip, remote_user, remote_pwd)

    result = virsh.dominfo(vm_ref, ignore_status=True, debug=True, uri=remote_uri)
    status = result.exit_status
    output = result.stdout.strip()
    err = result.stderr.strip()

    # recover libvirtd service start
    if libvirtd == "off":
        utils_libvirtd.libvirtd_start()

    # check status_error
    if status_error == "yes":
        if status == 0 or err == "":
            test.fail("Run successfully with wrong command!")
    elif status_error == "no":
        if status != 0 or output == "":
            test.fail("Run failed with right command")
