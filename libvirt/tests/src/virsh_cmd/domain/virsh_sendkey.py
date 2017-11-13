import logging
import time

from virttest import virsh
from virttest import utils_package

from provider import libvirt_version


def run(test, params, env):
    """
    Test send-key command, include all types of codeset and sysrq

    For normal sendkey test, we create a file to check the command
    execute by send-key. For sysrq test, check the /var/log/messages
    and guest status
    """

    if not virsh.has_help_command('send-key'):
        test.cancel("This version of libvirt does not support the send-key "
                    "test")

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    status_error = ("yes" == params.get("status_error", "no"))
    keystrokes = params.get("sendkey", "")
    codeset = params.get("codeset", "")
    holdtime = params.get("holdtime", "")
    sysrq_test = ("yes" == params.get("sendkey_sysrq", "no"))
    sleep_time = int(params.get("sendkey_sleeptime", 2))
    readonly = params.get("readonly", False)
    username = params.get("username")
    password = params.get("password")
    create_file = params.get("create_file_name")
    uri = params.get("virsh_uri")
    simultaneous = params.get("sendkey_simultaneous", "yes") == "yes"
    unprivileged_user = params.get('unprivileged_user')
    if unprivileged_user:
        if unprivileged_user.count('EXAMPLE'):
            unprivileged_user = 'testacl'

    if not libvirt_version.version_compare(1, 1, 1):
        if params.get('setup_libvirt_polkit') == 'yes':
            test.cancel("API acl test not supported in current libvirt "
                        "version.")

    def send_line(send_str):
        """
        send string to guest with send-key and end with Enter
        """
        for send_ch in list(send_str):
            virsh.sendkey(vm_name, "KEY_%s" % send_ch.upper(),
                          ignore_status=False)

        virsh.sendkey(vm_name, "KEY_ENTER",
                      ignore_status=False)

    vm = env.get_vm(vm_name)
    session = vm.wait_for_login()

    if sysrq_test:
        # Is 'rsyslog' installed on guest? It'll be what writes out
        # to /var/log/messages
        if not utils_package.package_install("rsyslog", session):
            test.fail("Fail to install rsyslog, make sure that you have "
                      "usable repo in guest")

        # clear messages, restart rsyslog, and make sure it's running
        session.cmd("echo '' > /var/log/messages")
        session.cmd("service rsyslog restart")
        ps_stat = session.cmd_status("ps aux |grep rsyslog")
        if ps_stat != 0:
            test.fail("rsyslog is not running in guest")

        # enable sysrq
        session.cmd("echo 1 > /proc/sys/kernel/sysrq")

    # make sure the environment is clear
    if create_file is not None:
        session.cmd("rm -rf %s" % create_file)

    try:
        # wait for tty started
        tty_stat = "ps aux|grep tty"
        timeout = 60
        while timeout >= 0 and \
                session.get_command_status(tty_stat) != 0:
            time.sleep(1)
            timeout = timeout - 1
        if timeout < 0:
            test.fail("Can not wait for tty started in 60s")

        # send user and passwd to guest to login
        send_line(username)
        time.sleep(2)
        send_line(password)
        time.sleep(2)

        if sysrq_test or simultaneous:
            output = virsh.sendkey(vm_name, keystrokes, codeset=codeset,
                                   holdtime=holdtime, readonly=readonly,
                                   unprivileged_user=unprivileged_user,
                                   uri=uri)
        else:
            # If multiple keycodes are specified, they are all sent
            # simultaneously to the guest, and they may be received
            # in random order. If you need distinct keypresses, you
            # must use multiple send-key invocations.
            for keystroke in keystrokes.split():
                output = virsh.sendkey(vm_name, keystroke, codeset=codeset,
                                       holdtime=holdtime, readonly=readonly,
                                       unprivileged_user=unprivileged_user,
                                       uri=uri)
                if output.exit_status:
                    test.fail("Failed to send key %s to guest: %s" %
                              (keystroke, output.stderr))
        time.sleep(sleep_time)
        if output.exit_status != 0:
            if status_error:
                logging.info("Failed to sendkey to guest as expected, Error:"
                             "%s.", output.stderr)
                return
            else:
                test.fail("Failed to send key to guest, Error:%s." %
                          output.stderr)
        elif status_error:
            test.fail("Expect fail, but succeed indeed.")

        if create_file is not None:
            # check if created file exist
            cmd_ls = "ls %s" % create_file
            sec_status, sec_output = session.get_command_status_output(cmd_ls)
            if sec_status == 0:
                logging.info("Succeed to create file with send key")
            else:
                test.fail("Fail to create file with send key, Error:%s" %
                          sec_output)
        elif sysrq_test:
            # check /var/log/message info according to different key

            # Since there's no guarantee when messages will be written
            # we'll do a check and wait loop for up to 60 seconds
            timeout = 60
            while timeout >= 0:
                if "KEY_H" in keystrokes:
                    get_status = session.cmd_status("cat /var/log/messages|"
                                                    "grep 'SysRq.*HELP'")
                elif "KEY_M" in keystrokes:
                    get_status = session.cmd_status("cat /var/log/messages|"
                                                    "grep 'SysRq.*Show Memory'")
                elif "KEY_T" in keystrokes:
                    get_status = session.cmd_status("cat /var/log/messages|"
                                                    "grep 'SysRq.*Show State'")
                elif "KEY_B" in keystrokes:
                    session = vm.wait_for_login()
                    result = virsh.domstate(vm_name, '--reason', ignore_status=True)
                    output = result.stdout.strip()
                    logging.debug("The guest state: %s", output)
                    if not output.count("booted"):
                        get_status = 1
                    else:
                        get_status = 0
                        session.close()

                if get_status == 0:
                    timeout = -1
                else:
                    session.cmd("echo \"virsh sendkey waiting\" >> /var/log/messages")
                    time.sleep(1)
                    timeout = timeout - 1

            if get_status != 0:
                test.fail("SysRq does not take effect in guest, keystrokes is "
                          "%s" % keystrokes)
            else:
                logging.info("Succeed to send SysRq command")
        else:
            test.fail("Test cfg file invalid: either sysrq_params or "
                      "create_file_name must be defined")

    finally:
        if create_file is not None:
            session.cmd("rm -rf %s" % create_file)
        session.close()
