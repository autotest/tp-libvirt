import os
import subprocess
import logging
import time
import locale

from virttest import virsh
from virttest import data_dir
from virttest import migration
from virttest import ssh_key


def run(test, params, env):
    """
    Test command: virsh domjobabort.

    The command can abort the currently running domain job.
    1.Prepare test environment,destroy or suspend a VM.
    2.Do action to get a subprocess(dump, save, managedsave).
    3.Perform virsh domjobabort operation to abort VM's job.
    4.Recover the VM's status and wait for the subprocess over.
    5.Confirm the test result.
    """

    vm_name = params.get("main_vm", "vm1")
    vm = env.get_vm(vm_name)
    start_vm = params.get("start_vm")
    pre_vm_state = params.get("pre_vm_state", "start")
    readonly = ("yes" == params.get("readonly", "no"))
    if start_vm == "no" and vm.is_alive():
        vm.destroy()

    # Instead of "paused_after_start_vm", use "pre_vm_state".
    # After start the VM, wait for some time to make sure the job
    # can be created on this domain.
    if start_vm == "yes":
        vm.wait_for_login()
        if params.get("pre_vm_state") == "suspend":
            vm.pause()

    domid = vm.get_id()
    domuuid = vm.get_uuid()
    original_speed = virsh.migrate_getspeed(vm_name).stdout.strip()

    def get_subprocess(action, vm_name, file, remote_uri=None):
        """
        Execute background virsh command, return subprocess w/o waiting for exit()

        :param cmd : virsh command.
        :param guest_name : VM's name
        :param file_source : virsh command's file option.
        """
        args = ""
        if action == "managedsave":
            file = ""
        elif action == "migrate":
            # Slow down migration for domjobabort
            virsh.migrate_setspeed(vm_name, "1")
            file = remote_uri
            args = "--unsafe"
        command = "virsh %s %s %s %s" % (action, vm_name, file, args)
        logging.debug("Action: %s", command)
        p = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)
        return p

    action = params.get("jobabort_action", "dump")
    dump_opt = params.get("dump_opt", None)
    status_error = params.get("status_error", "no")
    job = params.get("jobabort_job", "yes")
    tmp_file = os.path.join(data_dir.get_tmp_dir(), "domjobabort.tmp")
    tmp_pipe = os.path.join(data_dir.get_tmp_dir(), "domjobabort.fifo")
    vm_ref = params.get("jobabort_vm_ref")
    remote_uri = params.get("jobabort_remote_uri")
    remote_host = params.get("migrate_dest_host")
    remote_user = params.get("migrate_dest_user", "root")
    remote_pwd = params.get("migrate_dest_pwd")
    saved_data = None

    # Build job action
    if dump_opt:
        action = "dump --crash"

    if action == "managedsave":
        tmp_pipe = '/var/lib/libvirt/qemu/save/%s.save' % vm.name

    if action == "restore":
        virsh.save(vm_name, tmp_file, ignore_status=True)

    if action == "migrate":
        if remote_host.count("EXAMPLE"):
            test.cancel("Remote host should be configured "
                        "for migrate.")
        else:
            # Config ssh autologin for remote host
            ssh_key.setup_ssh_key(remote_host, remote_user,
                                  remote_pwd, port=22)

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

    # Get the subprocess of VM.
    # The command's effect is to abort the currently running domain job.
    # So before do "domjobabort" action, we must create a job on the domain.
    process = None
    if job == "yes" and start_vm == "yes" and status_error == "no":
        if os.path.exists(tmp_pipe):
            os.unlink(tmp_pipe)
        os.mkfifo(tmp_pipe)

        process = get_subprocess(action, vm_name, tmp_pipe, remote_uri)

        saved_data = None
        if action == "restore":
            with open(tmp_file, 'r') as tmp_f:
                saved_data = tmp_f.read(10 * 1024 * 1024)
            f = open(tmp_pipe, 'w')
            f.write(saved_data[:1024 * 1024])
        elif action == "migrate":
            f = None
        else:
            f = open(tmp_pipe, 'rb')
            dummy = f.read(1024 * 1024).decode(locale.getpreferredencoding(), 'ignore')

    # Give enough time for starting job
    t = 0
    while t < 5:
        jobtype = vm.get_job_type()
        if "None" == jobtype:
            t += 1
            time.sleep(1)
            continue
        elif jobtype is False:
            logging.error("Get job type failed.")
            break
        else:
            logging.debug("Job started: %s", jobtype)
            break
    virsh_dargs = {'ignore_status': True, 'debug': True}
    if readonly:
        virsh_dargs.update({'readonly': True})
    ret = virsh.domjobabort(vm_ref, **virsh_dargs)
    status = ret.exit_status

    if process and f:
        if saved_data:
            f.write(saved_data[1024 * 1024:])
        else:
            dummy = f.read()
        f.close()

        try:
            os.unlink(tmp_pipe)
        except OSError as detail:
            logging.info("Can't remove %s: %s", tmp_pipe, detail)
        try:
            os.unlink(tmp_file)
        except OSError as detail:
            logging.info("Cant' remove %s: %s", tmp_file, detail)

    # Recover the environment.
    if pre_vm_state == "suspend":
        vm.resume()
    if process:
        if process.poll():
            try:
                process.kill()
            except OSError:
                pass

    if action == "migrate":
        # Recover migration speed
        virsh.migrate_setspeed(vm_name, original_speed)
        migration.MigrationTest().cleanup_dest_vm(vm, None, remote_uri)

    # check status_error
    if status_error == "yes":
        if status == 0:
            test.fail("Run successfully with wrong command!")
    elif status_error == "no":
        if status != 0:
            test.fail("Run failed with right command")
