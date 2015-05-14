import re
import logging
import time
from autotest.client.shared import error
from virttest import virsh
from virttest import libvirt_vm
from virttest import remote
from virttest import utils_libvirtd
from virttest.libvirt_xml import vm_xml


def run(test, params, env):
    """
    Test command: virsh list.

    1) Filt parameters according libvirtd's version
    2) Prepare domain's exist state:transient,managed-save.
    3) Prepare libvirt's status.
    4) Execute list command.
    5) Result check.
    """
    def list_local_domains_on_remote(options_ref, remote_ip,
                                     remote_passwd, local_ip):
        """
        Create a virsh list command and execute it on remote host.
        It will list local domains on remote host.

        :param options_ref:options in virsh list command.
        :param remote_ip:remote host's ip.
        :param remote_passwd:remote host's password.
        :param local_ip:local ip, to create uri in virsh list.
        :return:return status and output of the virsh list command.
        """
        complete_uri = libvirt_vm.complete_uri(local_ip)
        command_on_remote = ("virsh -c %s list %s"
                             % (complete_uri, options_ref))
        session = remote.remote_login(
            "ssh", remote_ip, "22", "root", remote_passwd, "#", timeout=60)
        time.sleep(5)
        status, output = session.cmd_status_output(
            command_on_remote, internal_timeout=5)
        time.sleep(5)
        session.close()
        return int(status), output

    vm_name = params.get("main_vm", "virt-tests-vm1")
    options_ref = params.get("list_options_ref", "")
    list_ref = params.get("list_type_ref", "")
    vm_ref = params.get("vm_ref", "")
    status_error = params.get("status_error", "no")
    addition_status_error = params.get("addition_status_error", "no")
    desc = params.get("list_desc", "")
    libvirtd = params.get("libvirtd", "on")
    remote_ref = params.get("remote_ref", "")
    remote_ip = params.get("remote_ip", "REMOTE.EXAMPLE.COM")
    remote_pwd = params.get("remote_pwd", None)
    local_ip = params.get("local_ip", "LOCAL.EXAMPLE.COM")

    vm = env.get_vm(vm_name)
    domuuid = vm.get_uuid()
    domid = vm.get_id()

    # Some parameters are not supported on old libvirt, skip them.
    help_info = virsh.help("list").stdout.strip()
    if vm_ref and not re.search(vm_ref, help_info):
        raise error.TestNAError("This version do not support vm type:%s"
                                % vm_ref)
    if list_ref and not re.search(list_ref, help_info):
        raise error.TestNAError("This version do not support list type:%s"
                                % list_ref)

    # If a transient domain is destroyed, it will disappear.
    if vm_ref == "transient" and options_ref == "inactive":
        logging.info("Set addition_status_error to yes")
        logging.info(
            "because transient domain will disappear after destroyed.")
        addition_status_error = "yes"

    if vm_ref == "transient":
        vm_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        vm.undefine()
    elif vm_ref == "managed-save":
        virsh.managedsave(vm_name, ignore_status=True, print_info=True)

    # run test case
    if list_ref == "--uuid":
        result_expected = domuuid
        logging.info("%s's uuid is: %s", vm_name, domuuid)
    elif list_ref == "--title":
        vm_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        if options_ref == "inactive":
            virsh.desc(vm_name, "--config --title", desc)
        else:
            virsh.desc(vm_name, "--live --title", desc)
        result_expected = desc
        logging.info("%s's title is: %s", vm_name, desc)
    else:
        result_expected = vm_name
        logging.info("domain's name is: %s", vm_name)

    if options_ref == "vm_id":
        logging.info("%s's running-id is: %s", vm_name, domid)
        options_ref = "%s %s" % (domid, list_ref)
    elif options_ref == "vm_uuid":
        logging.info("%s's uuid is: %s", vm_name, domuuid)
        options_ref = "%s %s" % (domuuid, list_ref)
    elif options_ref == "inactive":
        vm.destroy()
        options_ref = "--inactive %s" % list_ref
    elif options_ref == "vm_name":
        options_ref = "%s %s" % (vm_name, list_ref)
    elif options_ref == "all":
        options_ref = "--all %s" % list_ref
    elif options_ref == "":
        options_ref = "%s" % list_ref

    # Prepare libvirtd status
    if libvirtd == "off":
        utils_libvirtd.libvirtd_stop()

    if remote_ref == "remote":
        if remote_ip.count("EXAMPLE.COM") or local_ip.count("EXAMPLE.COM"):
            raise error.TestNAError(
                "Remote test parameters unchanged from default")
        logging.info("Execute virsh command on remote host %s.", remote_ip)
        status, output = list_local_domains_on_remote(
            options_ref, remote_ip, remote_pwd, local_ip)
        logging.info("Status:%s", status)
        logging.info("Output:\n%s", output)
    else:
        if vm_ref:
            options_ref = "%s --%s" % (options_ref, vm_ref)
        result = virsh.dom_list(
            options_ref, ignore_status=True, print_info=True)
        status = result.exit_status
        output = result.stdout.strip()

    # Recover libvirtd service status
    if libvirtd == "off":
        utils_libvirtd.libvirtd_start()

    # Recover of domain
    if vm_ref == "transient" or list_ref == "--title":
        vm_backup.sync()
    elif vm_ref == "managed-save":
        # Recover saved guest.
        virsh.managedsave_remove(vm_name, ignore_status=True, print_info=True)

    # Check result
    status_error = (status_error == "no") and (addition_status_error == "no")
    if vm_ref == "managed-save":
        saved_output = re.search(vm_name + "\s+saved", output)
        if saved_output:
            output = saved_output.group(0)
        else:
            output = ""

    if not status_error:
        if not status and re.search(result_expected, output):
            raise error.TestFail("Run successful with wrong command!")
    else:
        if status:
            raise error.TestFail("Run failed with right command.")
        if not re.search(result_expected, output):
            raise error.TestFail("Run successful but result is not expected.")
