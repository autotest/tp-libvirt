import re
import logging

import aexpect

from autotest.client.shared import error

from avocado.utils import process

from virttest import libvirt_vm
from virttest import virt_vm
from virttest import virsh
from virttest import remote
from virttest import utils_libvirtd
from virttest.libvirt_xml import vm_xml


def run(test, params, env):
    """
    Test command: virsh reboot.

    Run a reboot command in the target domain.
    1.Prepare test environment.
    2.When the libvirtd == "off", stop the libvirtd service.
    3.Perform virsh reboot operation.
    4.Recover test environment.(libvirts service)
    5.Confirm the test result.
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    # run test case
    libvirtd = params.get("libvirtd", "on")
    vm_ref = params.get("reboot_vm_ref")
    status_error = ("yes" == params.get("status_error"))
    extra = params.get("reboot_extra")
    remote_ip = params.get("remote_ip", "REMOTE.EXAMPLE.COM")
    local_ip = params.get("local_ip", "LOCAL.EXAMPLE.COM")
    remote_pwd = params.get("remote_pwd", "password")
    agent = ("yes" == params.get("reboot_agent", "no"))
    mode = params.get("reboot_mode", "")
    pre_domian_status = params.get("reboot_pre_domian_status", "running")
    xml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    try:
        # Add or remove qemu-agent from guest before test
        try:
            vm.prepare_guest_agent(channel=agent, start=agent)
        except virt_vm.VMError, e:
            logging.debug(e)
            # qemu-guest-agent is not available on REHL5
            raise error.TestNAError("qemu-guest-agent package is not available")

        if pre_domian_status == "shutoff":
            virsh.destroy(vm_name)
        if libvirtd == "off":
            utils_libvirtd.libvirtd_stop()

        domid = vm.get_id()
        domuuid = vm.get_uuid()
        if vm_ref == "id":
            vm_ref = domid
        elif vm_ref == "name":
            vm_ref = vm_name
        elif vm_ref == "uuid":
            vm_ref = domuuid
        elif vm_ref == "hex_id":
            vm_ref = hex(int(domid))
        elif vm_ref.find("invalid") != -1:
            vm_ref = params.get(vm_ref)
        elif vm_ref == "remote_name":
            if remote_ip.count("EXAMPLE.COM") or local_ip.count("EXAMPLE.COM"):
                raise error.TestNAError("remote_ip and/or local_ip parameters"
                                        " not changed from default values")
            complete_uri = libvirt_vm.complete_uri(local_ip)
            try:
                session = remote.remote_login("ssh", remote_ip, "22", "root",
                                              remote_pwd, "#")
                session.cmd_output('LANG=C')
                command = "virsh -c %s reboot %s %s" % (complete_uri, vm_name,
                                                        mode)
                status, output = session.cmd_status_output(command,
                                                           internal_timeout=5)
                session.close()
                if not status:
                    # the operation before the end of reboot
                    # may result in data corruption
                    vm.wait_for_login().close()
            except (remote.LoginError, process.CmdError, aexpect.ShellError), e:
                logging.error("Exception: %s", str(e))
                status = -1
        if vm_ref != "remote_name":
            vm_ref = "%s %s" % (vm_ref, extra)
            cmdresult = virsh.reboot(vm_ref, mode,
                                     ignore_status=True, debug=True)
            status = cmdresult.exit_status
            if status:
                logging.debug("Error status, cmd error: %s", cmdresult.stderr)
                if not virsh.has_command_help_match('reboot', '\s+--mode\s+'):
                    # old libvirt doesn't support reboot
                    status = -2
            else:
                vm.wait_for_login().close()
        output = virsh.dom_list(ignore_status=True).stdout.strip()

        # recover libvirtd service start
        if libvirtd == "off":
            utils_libvirtd.libvirtd_start()

        # check status_error
        if status_error:
            if not status:
                raise error.TestFail("Run successfully with wrong command!")
        else:
            if status or (not re.search(vm_name, output)):
                if status == -2:
                    raise error.TestNAError(
                        "Reboot command doesn't work on older libvirt versions")
                raise error.TestFail("Run failed with right command")
    finally:
        xml_backup.sync()
