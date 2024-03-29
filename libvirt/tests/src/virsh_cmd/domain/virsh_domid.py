import logging as log

from avocado.utils import process

from virttest import libvirt_vm
from virttest import remote
from virttest import virsh
from virttest import utils_libvirtd
from virttest import ssh_key
from virttest import libvirt_version


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test command: virsh domid.

    The command returns basic information about the domain.
    1.Prepare test environment.
    2.When the libvirtd == "off", stop the libvirtd service.
    3.Perform virsh domid operation.
    4.Recover test environment.
    5.Confirm the test result.
    """

    vm_name = params.get("main_vm", "vm1")
    vm = env.get_vm(vm_name)
    if vm.is_alive() and params.get("start_vm") == "no":
        vm.destroy()

    domid = vm.get_id()
    domuuid = vm.get_uuid()

    vm_ref = params.get("domid_vm_ref")
    extra = params.get("domid_extra", "")
    status_error = params.get("status_error", "no")
    libvirtd = params.get("libvirtd", "on")

    def remote_test(params, vm_name):
        """
        Test remote case.
        """
        remote_ip = params.get("remote_ip", "REMOTE.EXAMPLE.COM")
        local_ip = params.get("local_ip", "LOCAL.EXAMPLE.COM")
        remote_pwd = params.get("remote_pwd", "")
        local_pwd = params.get("local_pwd", "")
        status = 0
        output = ""
        err = ""
        try:
            if remote_ip.count("EXAMPLE.COM") or local_ip.count("EXAMPLE.COM"):
                test.cancel("remote_ip and/or local_ip parameters "
                            "not changed from default values.")
            uri = libvirt_vm.complete_uri(local_ip)
            session = remote.remote_login("ssh", remote_ip, "22", "root",
                                          remote_pwd, "#")
            session.cmd_output('LANG=C')

            # setup ssh auto login from remote machine to test machine
            ssh_key.setup_remote_ssh_key(remote_ip, "root", remote_pwd,
                                         local_ip, "root", local_pwd)

            command = "virsh -c %s domid %s" % (uri, vm_name)
            status, output = session.cmd_status_output(command,
                                                       internal_timeout=5)
            if status != 0:
                err = output
            session.close()
        except process.CmdError:
            status = 1
            output = ""
            err = "remote test failed"
        return status, output, err

    # run test case
    if vm_ref == "id":
        vm_ref = domid
    elif vm_ref.find("invalid") != -1:
        vm_ref = params.get(vm_ref)
    elif vm_ref == "name":
        vm_ref = "%s %s" % (vm_name, extra)
    elif vm_ref == "uuid":
        vm_ref = domuuid

    if libvirtd == "off":
        utils_libvirtd.libvirtd_stop()

    if vm_ref != "remote":
        result = virsh.domid(vm_ref, ignore_status=True)
        status = result.exit_status
        output = result.stdout.strip()
        err = result.stderr.strip()
    else:
        status, output, err = remote_test(params, vm_name)

    # recover libvirtd service start
    if libvirtd == "off":
        utils_libvirtd.libvirtd_start()

    # check status_error
    if status_error == "yes":
        if status == 0 or err == "":
            if libvirtd == "off" and libvirt_version.version_compare(5, 6, 0):
                logging.info("From libvirt version 5.6.0 libvirtd is restarted "
                             "and command should succeed")
            else:
                test.fail("Run successfully with wrong command!")
    elif status_error == "no":
        if status != 0 or output == "":
            test.fail("Run failed with right command")
