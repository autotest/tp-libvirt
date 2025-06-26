import os
import re
import random
import uuid
import logging as log
import time

from threading import Thread

from avocado.utils import process

from virttest import libvirt_version
from virttest import ssh_key
from virttest import virsh
from virttest import utils_test
from virttest import utils_misc
from virttest import utils_package
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.vsock import Vsock

# Control the timeout of commands in guest globally
CMD_TIMEOUT = 240.0

# Constants for nc-vsock setup and usage
NC_VSOCK_DIR = '/tmp/nc-vsock'
NC_VSOCK_CMD = os.path.join(NC_VSOCK_DIR, 'nc-vsock')
NC_VSOCK_SRV_OUT = os.path.join(NC_VSOCK_DIR, "server_out.txt")
NC_VSOCK_CLI_TXT = os.path.join(NC_VSOCK_DIR, "client_in.txt")
VSOCK_PORT = 1234


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test vsock device:

    1.Edit/start guest with vsock device.
    2.Hotplug/hotunplug vsock device.
    3.Coldplug/Coldunplug vsock device
    4.Check if hotplugged vsock communicates.
    """

    def remove_all_vsocks(vm, vmxml):
        """
        Removes all vsock devices from current domain
        to avoid failures due to original domain definition
        which must have been backed up earlier for correct
        restore.

        :param vm: the current test domain
        :param vmxml: VMXML of the current test domain
        :return: None
        """

        vmxml.remove_all_device_by_type('vsock')
        was_alive = vm.is_alive()
        vmxml.sync()
        if was_alive:
            vm.start()

    def env_setup():
        """
        1. Install build dependency for nc-vsock both on guest
        and host.
        2. Get nc-vsock code
        3. Build nc-vsock on both guest and host.
        :return: None
        """

        session = vm.wait_for_login()
        cmds = [
            'rpm -q lsof || yum -y install lsof',
            'rpm -q git || yum -y install git',
            'rpm -q make || yum -y install make',
            'rpm -q gcc && yum -y reinstall gcc || yum -y install gcc',
            'rm -rf %s' % NC_VSOCK_DIR,
            'git clone %s %s' % (git_repo, NC_VSOCK_DIR),
            'cd %s && make' % NC_VSOCK_DIR,
        ]
        for cmd in cmds:
            for where in ["guest", "host"]:
                session_arg = session if where == "guest" else None
                status, output = utils_misc.cmd_status_output(cmd, shell=True,
                                                              timeout=CMD_TIMEOUT,
                                                              session=session_arg)
                cancel_if_failed(cmd, status, output, where)

        session.close()

    def cancel_if_failed(cmd, status, output, where):
        """
        Cancel test execution as soon as command failed reporting output

        :param cmd: Command that was executed
        :param status: Exit status of command
        :param output: Output of command
        :param where: "guest" or "host"
        :return:
        """

        if status:
            test.cancel("Failed to run %s on %s output: %s" % (
                cmd,
                where,
                output
            ))

    def write_from_host_to_guest():
        """
        1. Create file for stdin to nc-vsock
        2. Invoke nc-vsock as client writing to guest cid
        :return msg: The message sent to the server
        """

        msg = "message from client"
        process.run('echo %s > %s' % (msg, NC_VSOCK_CLI_TXT), shell=True)
        output = process.run("%s %d %s < %s" % (NC_VSOCK_CMD, int(cid),
                                                VSOCK_PORT, NC_VSOCK_CLI_TXT),
                             shell=True).stdout_text
        logging.debug(output)
        process.system_output('cat %s' % NC_VSOCK_CLI_TXT)
        return msg

    def wait_for_guest_to_receive(server):
        """
        nc-vsock server finishes as soon as it received data.
        We report if it's still running.

        :param server: The started server instance accepting requests
        :return: Nothing
        """

        server.join(5)
        if server.is_alive():
            logging.debug("The server thread is still running in the guest.")

    def validate_data_transfer_by_vsock():
        """
        1. Setup nc-vsock on host and guest
        2. Start vsock server on guest (wait a bit)
        3. Send message from host to guest using correct cid
        4. Get received message from vsock server
        5. Verify message was sent
        """

        env_setup()

        session = vm.wait_for_login()

        def _start_vsock_server_in_guest():
            """
            Starts the nc-vsock server to listen on VSOCK_PORT in the guest.
            The server will stop as soon as it received message from host.
            :return:
            """

            session.cmd("%s -l %s > %s" % (NC_VSOCK_CMD, VSOCK_PORT, NC_VSOCK_SRV_OUT))

        server = Thread(target=_start_vsock_server_in_guest)
        server.start()
        time.sleep(5)

        sent_data = write_from_host_to_guest()
        wait_for_guest_to_receive(server)
        received_data = session.cmd_output('cat %s' % NC_VSOCK_SRV_OUT).strip()

        if not sent_data == received_data:
            test.fail("Data transfer error with vsock device\n"
                      "Sent: '%s'\n"
                      "Received:'%s'" % (sent_data, received_data))

        session.close()

    def managedsave_restore():
        """
        Check that vm can be saved and restarted with current configuration
        """

        result = virsh.managedsave(vm_name, debug=True)
        utils_test.libvirt.check_exit_status(result, expect_error=False)
        result = virsh.start(vm_name)
        utils_test.libvirt.check_exit_status(result, expect_error=False)

    def add_guest_user(name, passwd):
        """
        Added a user account in guest

        :param name: user name
        :param passwd: password of user account
        """
        try:
            session = vm.wait_for_login()
            session.cmd_output('useradd %s' % name)
        finally:
            session.close()
        virsh.set_user_password(vm_name, name, passwd, debug=True)

    def forward_vsock_to_sshd():
        """
        Run a service that forwards VSOCK <=> SSHD communication
        """

        session = vm.wait_for_login()
        try:
            session.cmd("socat VSOCK-LISTEN:22,reuseaddr,fork TCP:localhost:22 &")
        except Exception as e:
            test.fail("Forwarding vsock to sshd communication failed: %s" % e)

    def check_systemd_version():
        """
        Check the version of systemd inside the guest
        """
        try:
            session = vm.wait_for_login()
            systemd_version = session.cmd_output(systemd_version_cmd)
            logging.debug("systemd version of guest is %s" % systemd_version)
        finally:
            session.close()
        return systemd_version

    start_vm = params.get("start_vm", "no")
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(params["main_vm"])
    auto_cid = params.get("auto_cid", "no")
    status_error = params.get("status_error", "no") == "yes"
    edit_xml = params.get("edit_xml", "no") == "yes"
    option = params.get("option", "")
    git_repo = params.get("git_repo", "")
    invalid_cid = params.get("invalid_cid", "no") == "yes"
    managedsave = params.get("managedsave", "no") == "yes"
    no_vsock = params.get("no_vsock", "no") == "yes"
    vsock_num = params.get("num")
    communication = params.get("communication", "no") == "yes"
    detach_device_alias = params.get("detach_device_alias", "no") == "yes"
    guest_user = params.get("guest_user")
    guest_user_passwd = params.get("guest_user_passwd")
    systemd_version_cmd = params.get("systemd_version_cmd")
    # Backup xml file
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()

    remove_all_vsocks(vm, vmxml)

    # Define vsock device xml
    vsock_dev = Vsock()
    vsock_dev.model_type = "virtio"
    if process.run("modprobe vhost_vsock").exit_status != 0:
        test.fail("Failed to load vhost_vsock module")
    if invalid_cid:
        cid = "-1"
    else:
        cid = random.randint(3, 10)
        vsock_dev.cid = {'auto': auto_cid, 'address': cid}
        vsock_dev.alias = {'name': 'ua-' + str(uuid.uuid1())}
    logging.debug(vsock_dev)

    if start_vm == "no" and vm.is_alive():
        vm.destroy()

    try:
        if edit_xml:
            edit_status1 = libvirt.exec_virsh_edit(
                vm_name, [(r":/<devices>/s/$/%s" %
                           re.findall(r"<vsock.*<\/vsock>",
                                      str(vsock_dev), re.M
                                      )[0].replace("/", "\/"))])
            edit_status2 = True
            if vsock_num == 2:
                edit_status2 = libvirt.exec_virsh_edit(
                    vm_name, [(r":/<devices>/s/$/%s" %
                               re.findall(r"<vsock.*<\/vsock>",
                                          str(vsock_dev), re.M
                                          )[0].replace("/", "\/"))])
                logging.debug(vm_xml.VMXML.new_from_inactive_dumpxml(vm_name))

            if status_error:
                if edit_status1 or edit_status2:
                    test.fail("virsh edit should fail\n")
            else:
                if not edit_status1:
                    test.fail("Failed to edit vm xml with vsock device\n")
                else:
                    result = virsh.start(vm_name, debug=True)
                    utils_test.libvirt.check_exit_status(result, expect_error=False)
        elif guest_user:
            libvirt_version.is_libvirt_feature_supported(params)
            if not utils_package.package_install(["libvirt-ssh-proxy"]):
                test.cancel("Failed to install libvirt-ssh-proxy on host")

            vmxml.add_device(vsock_dev)
            vmxml.sync()
            logging.debug(vmxml)
            vm.start()
            vm_ip = vm.wait_for_get_address(0, timeout=60)
            vm.prepare_guest_agent()
            add_guest_user(guest_user, guest_user_passwd)
            ssh_key.setup_ssh_key(vm_ip, guest_user, guest_user_passwd)

            if int(check_systemd_version()) < 256:
                forward_vsock_to_sshd()

            # For convenienence in automation, Fully disable host key checking.
            ssh_proxy_cmd = ("ssh -o StrictHostKeyChecking=no %s@qemu/%s hostname"
                             % (guest_user, vm_name))
            if process.run(ssh_proxy_cmd, shell=True, ignore_status=True).exit_status != 0:
                test.fail("ssh proxy for vsock is not working.")
        else:
            session = vm.wait_for_login()
            session.close()
            result = virsh.attach_device(vm_name, vsock_dev.xml, flagstr=option, debug=True)
            utils_test.libvirt.check_exit_status(result, expect_error=False)
            if option == "--config":
                result = virsh.start(vm_name, debug=True)
                utils_test.libvirt.check_exit_status(result, expect_error=False)
            vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
            logging.debug(vmxml)
            vsock_list = vmxml.devices.by_device_tag("vsock")
            cid = vsock_list[0].cid['address']
            if 0 == len(vsock_list):
                test.fail("No vsock device found in live xml\n")
            if communication:
                validate_data_transfer_by_vsock()
            if managedsave and not no_vsock:
                managedsave_restore()

            def _detach_completed():
                status = process.run("lsof /dev/vhost-vsock", ignore_status=True, shell=True).exit_status
                return status == 1
            if detach_device_alias:
                result = virsh.detach_device_alias(
                    vm.name, vsock_dev.alias['name'], ignore_status=False,
                    debug=True, wait_for_event=True, event_timeout=20)
            else:
                result = virsh.detach_device(
                    vm_name, vsock_dev.xml, debug=True,
                    wait_for_event=True, event_timeout=20)
            utils_test.libvirt.check_exit_status(result, expect_error=False)
            utils_misc.wait_for(_detach_completed, timeout=20)
            vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
            vsock_list = vmxml.get_devices("vsock")
            if vsock_list:
                test.fail("Still find vsock device in live xml after hotunplug\n")
            if managedsave and no_vsock:
                managedsave_restore()
    finally:
        if vm.is_alive():
            vm.destroy(gracefully=False)
        backup_xml.sync()
