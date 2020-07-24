import logging

from avocado.utils import process

from virttest import virsh
from virttest import utils_libvirtd
from virttest import ssh_key
from virttest import libvirt_version


def run(test, params, env):
    """
    Test command: virsh vcpuinfo.

    The command can get domain vcpu information
    1.Prepare test environment.
    2.When the ibvirtd == "off", stop the libvirtd service.
    3.Perform virsh vcpuinfo operation.
    4.Recover test environment.
    5.Confirm the test result.
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    status_error = params.get("status_error", "no")
    libvirtd = params.get("libvirtd", "on")
    if libvirtd == "off":
        utils_libvirtd.libvirtd_stop()

    # run test case
    vm_ref = params.get("vcpuinfo_vm_ref")
    domid = vm.get_id()
    domuuid = vm.get_uuid()

    def remote_case(params, vm_name):
        """
        Test remote case.
        """
        remote_ip = params.get("remote_ip", "REMOTE.EXAMPLE.COM")
        remote_pwd = params.get("remote_pwd", None)
        local_ip = params.get("local_ip", "LOCAL.EXAMPLE.COM")
        local_pwd = params.get("local_pwd", None)
        # Used for connecting from remote to local
        connect_uri = params.get("remote_connect_uri",
                                 "qemu+ssh://LOCAL.EXAMPLE.COM/system")
        # Verify connect_uri/remote_ip/local_ip is useful for this test.
        if ("EXAMPLE" in remote_ip or "EXAMPLE" in connect_uri
                or "EXAMPLE" in local_ip):
            test.cancel("Please set remote_ip or connect_uri or local_ip.")

        status = 0
        output = ""
        err = ""
        try:
            ssh_key.setup_remote_ssh_key(remote_ip, "root", remote_pwd,
                                         local_ip, "root", local_pwd)
            vcback = virsh.VirshConnectBack(remote_ip=remote_ip,
                                            remote_pwd=remote_pwd,
                                            uri=connect_uri,
                                            debug=True,
                                            ignore_status=True)
            cmdresult = vcback.vcpuinfo(vm_name)
            status = cmdresult.exit_status
            output = cmdresult.stdout.strip()
            vcback.close_session()
            if status != 0:
                err = output
        except process.CmdError:
            status = 1
            output = ""
            err = "remote test failed"
        # Maintain result format conformance with local test
        return status, output, err

    if vm_ref == "id":
        vm_ref = domid
    elif vm_ref == "hex_id":
        vm_ref = hex(int(domid))
    elif vm_ref.find("invalid") != -1:
        vm_ref = params.get(vm_ref)
    elif vm_ref == "uuid":
        vm_ref = domuuid
    elif vm_ref == "name":
        vm_ref = "%s %s" % (vm_name, params.get("vcpuinfo_extra"))

    if vm_ref == "remote":
        # Keep status_error check conditions (below) simple
        status, output, err = remote_case(params, vm_name)
    else:
        result = virsh.vcpuinfo(vm_ref)
        status = result.exit_status
        output = result.stdout.strip()
        err = result.stderr.strip()

    # recover libvirtd service start
    if libvirtd == "off":
        utils_libvirtd.libvirtd_start()

    # check status_error
    if status_error == "yes":
        if not status:
            if libvirtd == "off" and libvirt_version.version_compare(5, 6, 0):
                logging.debug("From libvirt version 5.6.0 libvirtd is restarted "
                              "and command should succeed")
            else:
                logging.debug(result)
                test.fail("Run successfully with wrong command!")
        # Check the error message in negative case.
        if not err and not libvirt_version.version_compare(5, 6, 0):
            logging.debug(result)
            logging.debug("Bugzilla: https://bugzilla.redhat.com/show_bug.cgi?id=889276 "
                          "is helpful for tracing this bug.")
            test.fail("No error message for a command error!")
    elif status_error == "no":
        if status:
            logging.debug(result)
            test.fail("Run failed with right command")
