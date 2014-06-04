import logging
import time
from autotest.client.shared import error
from virttest import virsh
from provider import libvirt_version


def run(test, params, env):
    """
    Test send-key command, include all types of codeset and sysrq

    For normal sendkey test, we create a file to check the command
    execute by send-key. For sysrq test, check the /var/log/messages
    and guest status
    """

    if not virsh.has_help_command('send-key'):
        raise error.TestNAError("This version of libvirt does not support "
                                "the send-key test")

    vm_name = params.get("main_vm", "virt-tests-vm1")
    status_error = ("yes" == params.get("status_error", "no"))
    options = params.get("sendkey_options", "")
    sysrq_test = ("yes" == params.get("sendkey_sysrq", "no"))
    readonly = params.get("readonly", False)
    username = params.get("username")
    password = params.get("password")
    create_file = params.get("create_file_name")
    uri = params.get("virsh_uri")
    unprivileged_user = params.get('unprivileged_user')
    if unprivileged_user:
        if unprivileged_user.count('EXAMPLE'):
            unprivileged_user = 'testacl'

    if not libvirt_version.version_compare(1, 1, 1):
        if params.get('setup_libvirt_polkit') == 'yes':
            raise error.TestNAError("API acl test not supported in current"
                                    + " libvirt version.")

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
        rpm_stat = session.cmd_status("rpm -q rsyslog")
        if rpm_stat != 0:
            logging.debug("rsyslog not found in guest installing")
            stat_install = session.cmd_status("yum install -y rsyslog", 300)
            if stat_install != 0:
                raise error.TestFail("Fail to install rsyslog, make"
                                     "sure that you have usable repo in guest")

        # clear messages, restart rsyslog, and make sure it's running
        session.cmd("echo '' > /var/log/messages")
        session.cmd("service rsyslog restart")
        ps_stat = session.cmd_status("ps aux |grep rsyslog")
        if ps_stat != 0:
            raise error.TestFail("rsyslog is not running in guest")

        # enable sysrq
        session.cmd("echo 1 > /proc/sys/kernel/sysrq")

    # make sure the environment is clear
    if create_file is not None:
        session.cmd("rm -rf %s" % create_file)

    try:
        # wait for tty1 started
        tty1_stat = "ps aux|grep [/]sbin/.*tty.*tty1"
        timeout = 60
        while timeout >= 0 and \
                session.get_command_status(tty1_stat) != 0:
            time.sleep(1)
            timeout = timeout - 1
        if timeout < 0:
            raise error.TestFail("Can not wait for tty1 started in 60s")

        # send user and passwd to guest to login
        send_line(username)
        time.sleep(2)
        send_line(password)
        time.sleep(2)

        output = virsh.sendkey(vm_name, options, readonly=readonly,
                               unprivileged_user=unprivileged_user,
                               uri=uri)
        time.sleep(2)
        if output.exit_status != 0:
            if status_error:
                logging.info("Failed to sendkey to guest as expected, Error:"
                             "%s.", output.stderr)
                return
            else:
                raise error.TestFail("Failed to send key to guest, Error:%s." %
                                     output.stderr)
        elif status_error:
            raise error.TestFail("Expect fail, but succeed indeed.")

        if create_file is not None:
            # check if created file exist
            cmd_ls = "ls %s" % create_file
            sec_status, sec_output = session.get_command_status_output(cmd_ls)
            if sec_status == 0:
                logging.info("Succeed to create file with send key")
            else:
                raise error.TestFail("Fail to create file with send key, "
                                     "Error:%s" % sec_output)
        elif sysrq_test:
            # check /var/log/message info according to different key

            # Since there's no guarantee when messages will be written
            # we'll do a check and wait loop for up to 60 seconds
            timeout = 60
            while timeout >= 0:
                if "KEY_H" in options:
                    get_status = session.cmd_status("cat /var/log/messages|"
                                                    "grep 'SysRq.*HELP'")
                elif "KEY_M" in options:
                    get_status = session.cmd_status("cat /var/log/messages|"
                                                    "grep 'SysRq.*Show Memory'")
                elif "KEY_T" in options:
                    get_status = session.cmd_status("cat /var/log/messages|"
                                                    "grep 'SysRq.*Show State'")

                if get_status == 0:
                    timeout = -1
                else:
                    session.cmd("echo \"virsh sendkey waiting\" >> /var/log/messages")
                    time.sleep(1)
                    timeout = timeout - 1

            if get_status != 0:
                raise error.TestFail("SysRq does not take effect in guest, "
                                     "options is %s" % options)
            else:
                logging.info("Succeed to send SysRq command")
        else:
            raise error.TestFail("Test cfg file invalid: either sysrq_params "
                                 "or create_file_name must be defined")

    finally:
        if create_file is not None:
            session.cmd("rm -rf %s" % create_file)
        session.close()
