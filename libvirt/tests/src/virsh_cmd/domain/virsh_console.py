import logging
import platform

import aexpect

from avocado.utils import process

from virttest import utils_test
from virttest import libvirt_version
from virttest.libvirt_xml import vm_xml


CMD_TIMEOUT = 30


def xml_console_config(vm_name, serial_type='pty',
                       serial_port='0', serial_path=None):
    """
    Check the primary serial and set it to pty.
    """
    vm_xml.VMXML.set_primary_serial(vm_name, serial_type,
                                    serial_port, serial_path)


def xml_console_recover(vmxml):
    """
    Recover older xml config with backup vmxml.
    """
    vmxml.undefine()
    if vmxml.define():
        return True
    else:
        logging.error("Recover older serial failed:%s.", vmxml.get('xml'))
        return False


def vm_console_config(vm, test, device='ttyS0',
                      speed='115200', guest_arch='x86_64'):
    """
    Login to config vm for virsh console.
    Three step:
    1)Add dev to /etc/securetty to support 'root' for virsh console
    2)Add kernel console:
      e.g. console=ttyS0,115200
    3)Config init process for RHEL5,4,3 and others.
      e.g. S0:2345:respawn:/sbin/mingetty ttyS0

    :param vm: Libvirt VM Object
    :param test: Test Object
    :param device: console device to be configured
    :param speed: console baud rate to be configured
    :param guest_arch: architecture of the guest vm

    :return: True on configuration success
    :raise: test.fail on configuration failure
    """
    if not vm.is_alive():
        vm.start(autoconsole=False)
        vm.wait_for_login()

    # Step 1
    vm.set_root_serial_console(device)

    # Step 2
    if not vm.set_kernel_console(device, speed, guest_arch_name=guest_arch):
        test.fail("Config kernel for console failed.")

    # Step 3
    if not vm.set_console_getty(device):
        test.fail("Config getty for console failed.")

    vm.destroy()
    # Confirm vm is down
    vm.wait_for_shutdown()
    return True


def check_duplicated_console(command, force_command, status_error, login_user,
                             login_passwd, test):
    """
    Test opening a second console with another console active using --force
    option or not.

    :param command: Test command without --force option
    :param force_command: Test command with --force option
    :param status_error: Whether the command is fault.
    :param login_user: User name for logging into the VM.
    :param login_passwd: Password for logging into the VM.
    :param test: Test Object
    """
    session = aexpect.ShellSession(command)
    if not status_error:
        # Test duplicated console session
        res = process.run(command, timeout=CMD_TIMEOUT,
                          ignore_status=True, shell=True)
        logging.debug(res)
        if res.exit_status == 0:
            test.fail("Duplicated console session should fail. "
                      "but succeeded with:\n%s" % res)

        # Test duplicated console session with force option
        force_session = aexpect.ShellSession(force_command)
        force_status = utils_test.libvirt.verify_virsh_console(
            force_session, login_user, login_passwd,
            timeout=CMD_TIMEOUT, debug=True)
        if not force_status:
            test.fail("Expect force console session should succeed, "
                      "but failed.")
        force_session.close()
    session.close()


def check_disconnect_on_shutdown(command, status_error, login_user,
                                 login_passwd, test):
    """
    Test whether an active console will disconnect after shutting down the VM.

    :param command: Test command without --force option
    :param status_error: Whether the command is fault.
    :param login_user: User name for logging into the VM.
    :param login_passwd: Password for logging into the VM.
    :param test: Test Object
    """
    session = aexpect.ShellSession(command)
    if not status_error:
        log = ""
        console_cmd = "shutdown -h now"
        try:
            while True:
                match, text = session.read_until_last_line_matches(
                    [r"[E|e]scape character is", r"login:",
                     r"[P|p]assword:", session.prompt],
                    10, internal_timeout=1)

                if match == 0:
                    logging.debug("Got '^]', sending '\\n'")
                    session.sendline()
                elif match == 1:
                    logging.debug("Got 'login:', sending '%s'", login_user)
                    session.sendline(login_user)
                elif match == 2:
                    logging.debug("Got 'Password:', sending '%s'", login_passwd)
                    session.sendline(login_passwd)
                elif match == 3:
                    logging.debug("Got Shell prompt -- logged in")
                    break

            session.cmd_output(console_cmd, timeout=120)
            # On newer OS like RHEL7, 'shutdown -h now' will not exit before
            # machine shutdown. But on RHEL6, 'shutdown -h now' will exit and
            # allow user to execute another command. This next command will not
            # exit before machine shutdown.
            session.cmd_output("echo $?")
            session.close()
            test.error('Do not expect any command can be executed '
                       'after successfully shutdown')
        except (aexpect.ShellError,
                aexpect.ExpectError) as detail:
            if 'Shell process terminated' not in str(detail):
                test.fail('Expect shell terminated, but found %s'
                          % detail)
            log = session.get_output()
            logging.debug("Shell terminated on VM shutdown:\n%s\n%s",
                          detail, log)
            session.close()


def run(test, params, env):
    """
    Test command: virsh console.
    """
    os_type = params.get("os_type")
    if os_type == "windows":
        test.cancel("SKIP:Do not support Windows.")

    # Get parameters for test
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    arch = params.get("vm_arch_name", "x86_64")

    vm_ref = params.get("virsh_console_vm_ref", "domname")
    vm_state = params.get("virsh_console_vm_state", "running")
    login_user = params.get("console_login_user", "root")
    if login_user == "root":
        login_passwd = params.get("password")
    else:
        login_passwd = params.get("console_password_not_root")
    status_error = "yes" == params.get("status_error", "no")
    domuuid = vm.get_uuid()
    domid = ""
    uri = params.get("virsh_uri")
    unprivileged_user = params.get('unprivileged_user')

    # PAPR guests don't usually have a true serial device (ttyS0),
    # they only have the hypervisor console (/dev/hvc0)
    if 'ppc64' in platform.machine():
        params["console_device"] = 'hvc0'

    update_console = params.get("update_console", "yes")
    console_dev = params.get("console_device", "ttyS0")
    console_speed = params.get("console_speed", "115200")
    if unprivileged_user:
        if unprivileged_user.count('EXAMPLE'):
            unprivileged_user = 'testacl'

    if not libvirt_version.version_compare(1, 1, 1):
        if params.get('setup_libvirt_polkit') == 'yes':
            test.cancel("API acl test not supported in current"
                        " libvirt version.")

    # A backup of original vm
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    if vm.is_alive():
        vm.destroy()
    if vm.is_qemu():
        xml_console_config(vm_name)

    try:
        # Guarantee cleanup after config vm console failed.
        if update_console == "yes":
            if vm.is_qemu():
                vm_console_config(vm, test, device=console_dev,
                                  speed=console_speed, guest_arch=arch)

        # Prepare vm state for test
        if vm_state != "shutoff":
            vm.start(autoconsole=False)
            if vm.is_qemu():
                # LXC cannot login here, because it will use virsh console
                # to login, it will break the console action in next step
                vm.wait_for_login()
            domid = vm.get_id()
        if vm_state == "paused":
            vm.pause()

        if vm_ref == "domname":
            vm_ref = vm_name
        elif vm_ref == "domid":
            vm_ref = domid
        elif vm_ref == "domuuid":
            vm_ref = domuuid
        elif domid and vm_ref == "hex_id":
            vm_ref = hex(int(domid))

        # Run command
        if params.get('setup_libvirt_polkit') == 'yes':
            cmd = "virsh -c %s console %s" % (uri, vm_ref)
            command = "su - %s -c '%s'" % (unprivileged_user, cmd)
            force_command = "su - %s -c '%s --force'" % (unprivileged_user, cmd)
        else:
            command = "virsh console %s" % vm_ref
            force_command = "virsh console %s --force" % vm_ref
        console_session = aexpect.ShellSession(command)

        status = utils_test.libvirt.verify_virsh_console(
            console_session, login_user, login_passwd,
            timeout=CMD_TIMEOUT, debug=True)
        console_session.close()

        check_duplicated_console(command, force_command, status_error,
                                 login_user, login_passwd, test)
        check_disconnect_on_shutdown(command, status_error, login_user,
                                     login_passwd, test)
    finally:
        # Recover state of vm.
        if vm_state == "paused":
            vm.resume()

        # clean up console from kernel commandline
        if not vm.is_alive():
            vm.start()
            vm.wait_for_login()
        if update_console == "yes":
            vm.set_kernel_console(console_dev, console_speed,
                                  remove=True, guest_arch_name=arch)

        # Recover vm
        if vm.is_alive():
            vm.destroy()
        if vm.is_qemu():
            xml_console_recover(vmxml_backup)

    # Check result
    if status_error:
        if status:
            test.fail("Run successful with wrong command!")
    else:
        if not status:
            test.fail("Run failed with right command!")
