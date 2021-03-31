import logging
import time

from virttest import virsh
from virttest import utils_package
from virttest import utils_test
from virttest import libvirt_version
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml

from avocado.utils import wait


def run(test, params, env):
    """
    Test send-key command, include all types of codeset and sysrq

    For normal sendkey test, we create a file to check the command
    execute by send-key. For sysrq test, check the /var/log/messages
    in RHEL or /var/log/syslog in Ubuntu and guest status
    """

    if not virsh.has_help_command('send-key'):
        test.cancel("This version of libvirt does not support the send-key "
                    "test")

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    status_error = ("yes" == params.get("status_error", "no"))
    keystrokes = params.get("sendkey", "")
    codeset = params.get("codeset", "")
    holdtime = params.get("holdtime", "")
    hold_timeout = eval(params.get("hold_timeout", "1"))
    sysrq_test = ("yes" == params.get("sendkey_sysrq", "no"))
    sleep_time = int(params.get("sendkey_sleeptime", 5))
    readonly = params.get("readonly", False)
    username = params.get("username")
    password = params.get("password")
    create_file = params.get("create_file_name")
    uri = params.get("virsh_uri")
    simultaneous = params.get("sendkey_simultaneous", "yes") == "yes"
    unprivileged_user = params.get('unprivileged_user')
    is_crash = ("yes" == params.get("is_crash", "no"))
    add_panic_device = ("yes" == params.get("add_panic_device", "yes"))
    panic_model = params.get('panic_model', 'isa')
    force_vm_boot_text_mode = ("yes" == params.get("force_vm_boot_text_mode", "yes"))
    crash_dir = "/var/crash"
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
    vm.wait_for_login().close()
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    if force_vm_boot_text_mode:
        # Boot the guest in text only mode so that send-key commands would succeed
        # in creating a file
        try:
            utils_test.update_boot_option(vm, args_added="3", guest_arch_name=params.get('vm_arch_name'))
        except Exception as info:
            test.error(info)

    session = vm.wait_for_login()
    if sysrq_test:
        # In postprocess of previous testcase would pause and resume the VM
        # that would change the domstate to running (unpaused) and cause
        # sysrq reboot testcase to fail as the domstate persist across reboot
        # so it is better to destroy and start VM before the test starts
        if "KEY_B" in keystrokes:
            cmd_result = virsh.domstate(vm_name, '--reason', ignore_status=True)
            if "unpaused" in cmd_result.stdout.strip():
                vm.destroy()
                vm.start()
                session = vm.wait_for_login()
        if is_crash:
            session.cmd("rm -rf {0}; mkdir {0}".format(crash_dir))
            libvirt.update_on_crash(vm_name, "destroy")
            if add_panic_device:
                libvirt.add_panic_device(vm_name, model=panic_model)
            if not vm.is_alive():
                vm.start()
            session = vm.wait_for_login()
        LOG_FILE = "/var/log/messages"
        if "ubuntu" in vm.get_distro().lower():
            LOG_FILE = "/var/log/syslog"
        # Is 'rsyslog' installed on guest? It'll be what writes out
        # to LOG_FILE
        if not utils_package.package_install("rsyslog", session):
            test.fail("Fail to install rsyslog, make sure that you have "
                      "usable repo in guest")

        # clear messages, restart rsyslog, and make sure it's running
        session.cmd("echo '' > %s" % LOG_FILE)
        # check the result of restart rsyslog
        status, output = session.cmd_status_output("service rsyslog restart")
        if status:
            # To avoid 'Exec format error'
            utils_package.package_remove("rsyslog", session)
            utils_package.package_install("rsyslog", session)
            # if rsyslog.service is masked, need to unmask rsyslog
            if "Unit rsyslog.service is masked" in output:
                session.cmd("systemctl unmask rsyslog")
            session.cmd("echo '' > %s" % LOG_FILE)
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
            if not wait.wait_for(lambda: session.get_command_status_output(cmd_ls),
                                 hold_timeout, step=5):
                test.fail("Fail to create file with send key")
            logging.info("Succeed to create file with send key")
        elif sysrq_test:
            # check LOG_FILE info according to different key

            # Since there's no guarantee when messages will be written
            # we'll do a check and wait loop for up to 60 seconds
            timeout = 60
            while timeout >= 0:
                if "KEY_H" in keystrokes:
                    cmd = "cat %s | grep -i 'SysRq.*HELP'" % LOG_FILE
                    get_status = session.cmd_status(cmd)
                elif "KEY_M" in keystrokes:
                    cmd = "cat %s | grep -i 'SysRq.*Show Memory'" % LOG_FILE
                    get_status = session.cmd_status(cmd)
                elif "KEY_T" in keystrokes:
                    cmd = "cat %s | grep -i 'SysRq.*Show State'" % LOG_FILE
                    get_status = session.cmd_status(cmd)
                    # Sometimes SysRq.*Show State string missed in LOG_FILE
                    # as a fall back check for runnable tasks logged
                    if get_status != 0:
                        cmd = "cat %s | grep 'runnable tasks:'" % LOG_FILE
                        get_status = session.cmd_status(cmd)

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
                # crash
                elif is_crash:
                    dom_state = virsh.domstate(vm_name, "--reason").stdout.strip()
                    logging.debug("domain state is %s" % dom_state)
                    if "crashed" in dom_state:
                        get_status = 0
                    else:
                        get_status = 1

                if get_status == 0:
                    timeout = -1
                else:
                    if not is_crash:
                        session.cmd("echo \"virsh sendkey waiting\" >> %s" % LOG_FILE)
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
            session = vm.wait_for_login()
            session.cmd("rm -rf %s" % create_file)
        session.close()
        vmxml_backup.sync()
