import os
import subprocess

from virttest import virsh
from virttest import data_dir
from virttest import ssh_key


def get_subprocess(action, vm_name, filepath):
    """
    Execute background virsh command, return subprocess w/o waiting for exit()

    :param action : virsh command.
    :param vm_name : VM's name
    :param filepath : virsh command's file option.
    """
    if action == "managedsave":
        filepath = ""
    if action == "restore":
        vm_name = ""
    command = "virsh %s %s %s" % (action, vm_name, filepath)
    p = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE)
    return p


def run(test, params, env):
    """
    Test command: virsh domcontrol.

    The command can show the state of a control interface to the domain.
    1.Prepare test environment, destroy or suspend a VM.
    2.Do action to get a subprocess(dump, save, restore, managedsave) if
      domcontrol_job is set as yes.
    3.Perform virsh domcontrol to check state of a control interface to the
      domain.
    4.Recover the VM's status and wait for the subprocess over.
    5.Confirm the test result.
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    start_vm = params.get("start_vm")
    pre_vm_state = params.get("pre_vm_state", "running")
    options = params.get("domcontrol_options", "")
    action = params.get("domcontrol_action", "dump")
    tmp_file = os.path.join(data_dir.get_tmp_dir(), "domcontrol.tmp")
    vm_ref = params.get("domcontrol_vm_ref")
    job = params.get("domcontrol_job", "yes")
    readonly = "yes" == params.get("readonly", "no")
    status_error = params.get("status_error", "no")
    remote_uri = params.get("remote_uri")
    remote_ip = params.get("remote_ip")
    remote_pwd = params.get("remote_pwd")
    remote_user = params.get("remote_user", "root")
    if start_vm == "no" and vm.is_alive():
        vm.destroy()

    if remote_uri:
        if remote_ip.count("EXAMPLE"):
            test.cancel("The remote ip is Sample one, pls configure it first")
        ssh_key.setup_ssh_key(remote_ip, remote_user, remote_pwd)

    # Instead of "paused_after_start_vm", use "pre_vm_state".
    # After start the VM, wait for some time to make sure the job
    # can be created on this domain.
    if start_vm == "yes":
        vm.wait_for_login()
        if params.get("pre_vm_state") == "suspend":
            vm.pause()

    domid = vm.get_id()
    domuuid = vm.get_uuid()

    if vm_ref == "id":
        vm_ref = domid
    elif vm_ref == "hex_id":
        vm_ref = hex(int(domid))
    elif vm_ref == "uuid":
        vm_ref = domuuid
    elif vm_ref.find("invalid") != -1:
        vm_ref = params.get(vm_ref)
    elif vm_ref == "name":
        vm_ref = vm_name

    if action == "managedsave":
        tmp_file = '/var/lib/libvirt/qemu/save/%s.save' % vm.name

    if action == "restore":
        virsh.save(vm_name, tmp_file, ignore_status=True)

    process = None
    if job == "yes" and start_vm == "yes" and status_error == "no":
        # Check domain contorl interface state with job on domain.
        process = get_subprocess(action, vm_name, tmp_file)
        while process.poll() is None:
            if vm.is_alive():
                ret = virsh.domcontrol(vm_ref, options, ignore_status=True,
                                       debug=True)
                status = ret.exit_status
                # check status_error
                if status != 0:
                    # Do not raise error if domain is not running, as save,
                    # managedsave and restore will change the domain state
                    # from running to shutoff or reverse, and the timing of
                    # the state change is not predicatable, so skip the error
                    # of domain state change and focus on domcontrol command
                    # status while domain is running.
                    if vm.is_alive():
                        test.fail("Run failed with right command")
    else:
        if remote_uri:
            # check remote domain status
            if not virsh.is_alive(vm_name, uri=remote_uri):
                # If remote domain is not running, start remote domain
                virsh.start(vm_name, uri=remote_uri)

        # Check domain control interface state without job on domain.
        ret = virsh.domcontrol(vm_ref, options, readonly=readonly,
                               ignore_status=True, debug=True, uri=remote_uri)
        status = ret.exit_status

        # check status_error
        if status_error == "yes":
            if status == 0:
                test.fail("Run successfully with wrong command!")
        elif status_error == "no":
            if status != 0:
                test.fail("Run failed with right command")

    # Recover the environment.
    if action == "managedsave":
        virsh.managedsave_remove(vm_name, ignore_status=True)
    if os.path.exists(tmp_file):
        os.unlink(tmp_file)
    if remote_uri:
        if virsh.is_alive(vm_name, uri=remote_uri):
            # Destroy remote domain
            virsh.destroy(vm_name, uri=remote_uri)
    if pre_vm_state == "suspend":
        vm.resume()
    if process:
        if process.poll() is None:
            process.kill()
