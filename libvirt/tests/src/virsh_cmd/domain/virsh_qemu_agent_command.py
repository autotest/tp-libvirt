import os
import logging
from autotest.client.shared import error, utils
from virttest import virsh
from virttest import utils_libvirtd
from virttest import libvirt_xml


def reset_domain(vm, vm_state, needs_agent=False):
    """
    Setup guest agent in domain.

    :param vm: the vm object
    :param vm_state: the given vm state string "shut off" or "running"
    """
    if vm.is_alive():
        vm.destroy()
    vm_xml = libvirt_xml.VMXML()
    vm_xml.new_from_dumpxml(vm.name)
    if needs_agent:
        logging.debug("Attempting to set guest agent channel")
        vm_xml.set_agent_channel(vm.name)
    if not vm_state == "shut off":
        vm.start()
        session = vm.wait_for_login()
        if needs_agent:
            # Check if qemu-ga already started automatically
            session = vm.wait_for_login()
            cmd = "rpm -q qemu-guest-agent || yum install -y qemu-guest-agent"
            stat_install = session.cmd_status(cmd, 300)
            if stat_install != 0:
                raise error.TestFail("Fail to install qemu-guest-agent, make "
                                     "sure that you have usable repo in guest")

            # Check if qemu-ga already started
            stat_ps = session.cmd_status("ps aux |grep [q]emu-ga")
            if stat_ps != 0:
                session.cmd("qemu-ga -d")
                # Check if the qemu-ga really started
                stat_ps = session.cmd_status("ps aux |grep [q]emu-ga")
                if stat_ps != 0:
                    raise error.TestFail("Fail to run qemu-ga in guest")
    if vm_state == "paused":
        vm.pause()


def reset_env(vm_name, xml_file):
    """
    Reset env

    :param vm_name: the vm name
    :xml_file: domain xml file
    """
    virsh.destroy(vm_name)
    virsh.undefine(vm_name)
    virsh.define(xml_file)
    if os.path.exists(xml_file):
        os.remove(xml_file)


def run(test, params, env):
    """
    Test command: virsh qemu-agent-command.
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vm_ref = params.get("vm_ref", "domname")
    vm_state = params.get("vm_state", "running")
    cmd = params.get("agent_cmd", "")
    options = params.get("options", "")
    needs_agent = "yes" == params.get("needs_agent", "yes")
    start_vm = "yes" == params.get("start_vm")
    status_error = "yes" == params.get("status_error", "no")
    if not status_error and options:
        option = options.split()[0]
        test_cmd = "qemu-agent-command"
        if virsh.has_command_help_match(test_cmd, option) is None:
            raise error.TestNAError("The current libvirt doesn't support"
                                    " %s option for %s" % (option, test_cmd))
    domuuid = vm.get_uuid()
    domid = ""
    xml_file = os.path.join(test.tmpdir, "vm.xml")
    virsh.dumpxml(vm_name, extra="--inactive", to_file=xml_file)
    libvirtd_inst = utils_libvirtd.Libvirtd()

    # Prepare domain
    try:
        reset_domain(vm, vm_state, needs_agent)
    except Exception, details:
        reset_env(vm_name, xml_file)
        raise error.TestFail(details)

    if vm_state != "shut off":
        domid = vm.get_id()

    if vm_ref == "domname":
        vm_ref = vm_name
    elif vm_ref == "domid":
        vm_ref = domid
    elif vm_ref == "domuuid":
        vm_ref = domuuid
    elif domid and vm_ref == "hex_id":
        vm_ref = hex(int(domid))

    try:
        # Run virsh command
        cmd_result = virsh.qemu_agent_command(vm_ref, cmd, options,
                                              ignore_status=True,
                                              debug=True)
        status = cmd_result.exit_status

        # Check result
        if not libvirtd_inst.is_running():
            raise error.TestFail("Libvirtd is not running after run command.")
        if status_error:
            if not status:
                # Bug 853673
                err_msg = "Expect fail but run successfully, please check Bug: "
                err_msg += "https://bugzilla.redhat.com/show_bug.cgi?id=853673"
                err_msg += " for more info"
                raise error.TestFail(err_msg)
            else:
                logging.debug("Command failed as expected.")
        else:
            if status:
                if "--async" in options:
                    err_msg = "Please check bug: "
                    err_msg += "https://bugzilla.redhat.com/show_bug.cgi?id="
                    err_msg += "1099060 for more info"
                    logging.error(err_msg)
                raise error.TestFail("Expect succeed, but run fail.")
    finally:
        # Cleanup
        reset_env(vm_name, xml_file)
        if not libvirtd_inst.is_running():
            libvirtd_inst.restart()
