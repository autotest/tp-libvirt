from avocado.utils import process

from virttest import libvirt_vm
from virttest import virsh
from virttest import remote
from virttest import utils_libvirtd
from virttest.libvirt_xml import vm_xml
from virttest import ssh_key


def run(test, params, env):
    """
    Test command: virsh vncdisplay.

    The command can output the IP address and port number for the VNC display.
    1.Prepare test environment.
    2.When the ibvirtd == "off", stop the libvirtd service.
    3.Perform virsh vncdisplay operation.
    4.Recover test environment.
    5.Confirm the test result.
    """

    vm_name = params.get("main_vm", "vm1")
    vm = env.get_vm(vm_name)

    libvirtd = params.get("libvirtd", "on")
    vm_ref = params.get("vncdisplay_vm_ref")
    status_error = params.get("status_error", "no")
    extra = params.get("vncdisplay_extra", "")

    def remote_case(params, vm_name):
        """
        Test remote case.
        """
        remote_ip = params.get("remote_ip", "REMOTE.EXAMPLE.COM")
        local_ip = params.get("local_ip", "LOCAL.EXAMPLE.COM")
        remote_pwd = params.get("remote_pwd", "")
        local_pwd = params.get("local_pwd", "")
        remote_user = params.get("remote_user", "root")
        local_user = params.get("local_user", "root")
        status = 0
        output = ""
        try:
            if remote_ip.count("EXAMPLE.COM") or local_ip.count("EXAMPLE.COM"):
                test.cancel("remote_ip and/or local_ip parameters "
                            "not changed from default values.")
            uri = libvirt_vm.complete_uri(local_ip)
            # setup ssh auto login
            ssh_key.setup_ssh_key(remote_ip, remote_user, remote_pwd)
            ssh_key.setup_remote_ssh_key(remote_ip, remote_user, remote_pwd,
                                         local_ip, local_user, local_pwd)
            session = remote.remote_login("ssh", remote_ip, "22", "root",
                                          remote_pwd, "#")
            session.cmd_output('LANG=C')
            command = "virsh -c %s vncdisplay %s" % (uri, vm_name)
            status, output = session.cmd_status_output(command,
                                                       internal_timeout=5)
            session.close()
        except process.CmdError:
            status = 1
            output = "remote test failed"
        return status, output

    xml_bak = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    is_alive = vm.is_alive()
    vmxml = xml_bak.copy()

    try:
        if not len(vmxml.get_graphics_devices("vnc")) and vm.is_qemu():
            graphic = vmxml.get_device_class('graphics')()
            graphic.add_graphic(vm_name, graphic="vnc")

        if is_alive and vm.is_dead():
            vm.start()
        domid = vm.get_id()
        domuuid = vm.get_uuid()

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

        if vm_ref == "remote":
            status, output = remote_case(params, vm_name)
        else:
            result = virsh.vncdisplay(vm_ref, ignore_status=True)
            status = result.exit_status
            output = result.stdout.strip()

    finally:
        if libvirtd == "off":
            utils_libvirtd.libvirtd_start()
        xml_bak.sync()

    # check status_error
    if status_error == "yes":
        if status == 0:
            test.fail("Run successfully with wrong command!")
    elif status_error == "no":
        if status != 0 or output == "":
            test.fail("Run failed with right command")
