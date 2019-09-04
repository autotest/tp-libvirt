from virttest import virsh
from virttest import utils_libvirtd
from virttest import ssh_key


def run(test, params, env):
    """
    Test command: virsh dommemstat.

    The command gets memory statistics for a domain
    1.Prepare test environment.
    2.When the ibvirtd == "off", stop the libvirtd service.
    3.Perform virsh dommemstat operation.
    4.Recover test environment.
    5.Confirm the test result.
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(params["main_vm"])

    domid = vm.get_id()
    domuuid = vm.get_uuid()

    status_error = params.get("status_error", "no")
    vm_ref = params.get("dommemstat_vm_ref", "name")
    libvirtd = params.get("libvirtd", "on")
    extra = params.get("dommemstat_extra", "")
    if libvirtd == "off":
        utils_libvirtd.libvirtd_stop()

    # run test case
    if vm_ref == "id":
        vm_ref = domid
    elif vm_ref == "hex_id":
        vm_ref = hex(int(domid))
    elif vm_ref == "uuid":
        vm_ref = domuuid
    elif vm_ref.count("_invalid_"):
        # vm_ref names parameter to fetch
        vm_ref = params.get(vm_ref)
    elif vm_ref == "name":
        vm_ref = "%s" % vm_name

    if libvirtd == "off":
        utils_libvirtd.libvirtd_stop()

    try:
        if vm_ref != "remote":
            status = virsh.dommemstat(vm_ref, extra, ignore_status=True,
                                      debug=True).exit_status
        else:
            remote_ip = params.get("remote_ip", "REMOTE.EXAMPLE.COM")
            remote_pwd = params.get("remote_pwd", None)
            remote_user = params.get("remote_user", "root")
            if remote_ip.count("EXAMPLE.COM"):
                test.cancel("remote ip parameters not set.")
            ssh_key.setup_ssh_key(remote_ip, remote_user, remote_pwd)
            remote_uri = "qemu+ssh://%s/system" % remote_ip
            virsh.start(vm_name, ignore_status=False, debug=True, uri=remote_uri)
            status = virsh.dommemstat(vm_name, ignore_status=True,
                                      debug=True, uri=remote_uri).exit_status
    finally:
        if vm_ref == "remote":
            virsh.destroy(vm_name, ignore_status=False, debug=True, uri=remote_uri)

    # recover libvirtd service start
    if libvirtd == "off":
        utils_libvirtd.libvirtd_start()

    # check status_error
    if status_error == "yes":
        if status == 0:
            test.fail("Run successfully with wrong command!")
    elif status_error == "no":
        if status != 0:
            test.fail("Run failed with right command")
