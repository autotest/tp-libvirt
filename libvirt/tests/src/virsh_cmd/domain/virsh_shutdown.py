from autotest.client.shared import error
from virttest import remote
from virttest import libvirt_vm
from virttest import virsh
from virttest import utils_libvirtd
from virttest.libvirt_xml import vm_xml
from provider import libvirt_version


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
    test_xml = xml_backup.copy()

    # Libvirt acl test related params
    uri = params.get("virsh_uri")
    unprivileged_user = params.get('unprivileged_user')
    if unprivileged_user:
        if unprivileged_user.count('EXAMPLE'):
            unprivileged_user = 'testacl'

    if not libvirt_version.version_compare(1, 1, 1):
        if params.get('setup_libvirt_polkit') == 'yes':
            raise error.TestNAError("API acl test not supported in current"
                                    + " libvirt version.")

    try:

        # Add or remove qemu-agent from guest before test
        if agent:
            test_xml.set_agent_channel()
            test_xml.sync()
        else:
            test_xml.remove_agent_channels()
            test_xml.sync()
        virsh.start(vm_name)
        guest_session = vm.wait_for_login()
        if agent:
            guest_session.cmd("qemu-ga -d")
            stat_ps = guest_session.cmd_status("ps aux |grep [q]emu-ga")
            guest_session.close()
            if stat_ps:
                raise error.TestError("Fail to start qemu-guest-agent!")
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
            status = virsh.shutdown(vm_ref, mode,
                                    unprivileged_user=unprivileged_user,
                                    uri=uri, debug=True,
                                    ignore_status=True).exit_status
        else:
            remote_ip = params.get("remote_ip", "REMOTE.EXAMPLE.COM")
            remote_pwd = params.get("remote_pwd", None)
            local_ip = params.get("local_ip", "LOCAL.EXAMPLE.COM")
            if remote_ip.count("EXAMPLE.COM") or local_ip.count("EXAMPLE.COM"):
                raise error.TestNAError(
                    "Remote test parameters unchanged from default")
            status = 0
            try:
                remote_uri = libvirt_vm.complete_uri(local_ip)
                session = remote.remote_login("ssh", remote_ip, "22", "root",
                                              remote_pwd, "#")
                session.cmd_output('LANG=C')
                command = ("virsh -c %s shutdown %s %s"
                           % (remote_uri, vm_name, mode))
                status = session.cmd_status(command, internal_timeout=5)
                session.close()
            except error.CmdError:
                status = 1

        # recover libvirtd service start
        if libvirtd == "off":
            utils_libvirtd.libvirtd_start()

        # check status_error
        if status_error:
            if not status:
                raise error.TestFail("Run successfully with wrong command!")
        else:
            if status:
                raise error.TestFail("Run failed with right command")
    finally:
        xml_backup.sync()
