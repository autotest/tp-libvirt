import logging

from avocado.utils import process

from virttest import remote
from virttest import libvirt_vm
from virttest import virsh
from virttest import utils_libvirtd
from virttest import ssh_key
from virttest import utils_misc
from virttest import libvirt_version
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Test command: virsh shutdown.

    The conmand can gracefully shutdown a domain.

    1.Prepare test environment.
    2.When the libvirtd == "off", stop the libvirtd service.
    3.Perform virsh setvcpus operation.
    4.Recover test environment.
    5.Confirm the test result.
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vm_ref = params.get("shutdown_vm_ref")
    status_error = ("yes" == params.get("status_error"))
    agent = ("yes" == params.get("shutdown_agent", "no"))
    mode = params.get("shutdown_mode", "")
    pre_domian_status = params.get("reboot_pre_domian_status", "running")
    libvirtd = params.get("libvirtd", "on")
    xml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    timeout = eval(params.get("shutdown_timeout", "60"))
    readonly = "yes" == params.get("shutdown_readonly", "no")
    expect_msg = params.get("shutdown_err_msg")

    # Libvirt acl test related params
    uri = params.get("virsh_uri")
    unprivileged_user = params.get('unprivileged_user')
    if unprivileged_user:
        if unprivileged_user.count('EXAMPLE'):
            unprivileged_user = 'testacl'

    if not libvirt_version.version_compare(1, 1, 1):
        if params.get('setup_libvirt_polkit') == 'yes':
            test.cancel("API acl test not supported in"
                        " current libvirt version.")

    try:
        # Add or remove qemu-agent from guest before test
        vm.prepare_guest_agent(channel=agent, start=agent)
        if pre_domian_status == "shutoff":
            virsh.destroy(vm_name)
        domid = vm.get_id()
        domuuid = vm.get_uuid()
        # run test case
        if vm_ref == "id":
            vm_ref = domid
        elif vm_ref == "hex_id":
            vm_ref = hex(int(domid))
        elif vm_ref.find("invalid") != -1:
            vm_ref = params.get(vm_ref)
        elif vm_ref == "name":
            vm_ref = "%s %s" % (vm_name, params.get("shutdown_extra"))
        elif vm_ref == "uuid":
            vm_ref = domuuid

        if libvirtd == "off":
            utils_libvirtd.libvirtd_stop()

        if vm_ref != "remote":
            result = virsh.shutdown(vm_ref, mode,
                                    unprivileged_user=unprivileged_user,
                                    uri=uri, debug=True,
                                    ignore_status=True,
                                    readonly=readonly)
            status = result.exit_status
        else:
            remote_ip = params.get("remote_ip", "REMOTE.EXAMPLE.COM")
            remote_pwd = params.get("remote_pwd", None)
            remote_user = params.get("remote_user", "root")
            local_ip = params.get("local_ip", "LOCAL.EXAMPLE.COM")
            local_pwd = params.get("local_pwd", "password")
            local_user = params.get("username", "root")
            if remote_ip.count("EXAMPLE.COM") or local_ip.count("EXAMPLE.COM"):
                test.cancel("Remote test parameters"
                            " unchanged from default")
            status = 0
            try:
                remote_uri = libvirt_vm.complete_uri(local_ip)
                # set up auto ssh login from remote machine to
                # execute commands
                config_opt = ["StrictHostKeyChecking=no"]
                ssh_key.setup_remote_ssh_key(remote_ip, remote_user,
                                             remote_pwd, hostname2=local_ip,
                                             user2=local_user,
                                             password2=local_pwd,
                                             config_options=config_opt)
                session = remote.remote_login("ssh", remote_ip, "22", "root",
                                              remote_pwd, "#")
                session.cmd_output('LANG=C')
                command = ("virsh -c %s shutdown %s %s"
                           % (remote_uri, vm_name, mode))
                status = session.cmd_status(command, internal_timeout=5)
                session.close()
            except process.CmdError:
                status = 1

        # recover libvirtd service start
        if libvirtd == "off":
            utils_libvirtd.libvirtd_start()

        # check status_error
        if status_error:
            if not status:
                if libvirtd == "off" and libvirt_version.version_compare(5, 6, 0):
                    logging.info("From libvirt version 5.6.0 libvirtd is restarted "
                                 "and command should succeed")
                else:
                    test.fail("Run successfully with wrong command!")
            if expect_msg:
                libvirt.check_result(result, expect_msg.split(';'))
        else:
            if status:
                test.fail("Run failed with right command")
            if not vm.wait_for_shutdown(timeout):
                test.fail("Failed to shutdown in timeout %s" % timeout)
    finally:
        if utils_misc.wait_for(utils_libvirtd.libvirtd_is_running, 60):
            xml_backup.sync()
