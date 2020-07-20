import os
import subprocess
import logging
import time
import locale

from virttest import virsh
from virttest import data_dir
from virttest import utils_libvirtd
from virttest import libvirt_version


def run(test, params, env):
    """
    Test command: virsh domjobinfo.

    The command returns information about jobs running on a domain.
    1.Prepare test environment.
    2.When the libvirtd == "off", stop the libvirtd service.
    3.Perform job action on a domain.
    4.Get running and completed job info by virsh domjobinfo.
    5.Recover test environment.
    6.Confirm the test result.
    """

    def get_subprocess(action, vm_name, file, remote_uri=None):
        """
        Execute background virsh command, return subprocess w/o waiting for exit()

        :param action : virsh command and its option.
        :param vm_name : VM's name
        :param file : virsh command's file option, could be vm.dump, vm.save, etc.
        """
        command = "virsh %s %s %s" % (action, vm_name, file)
        logging.debug("Action: %s", command)
        p = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)
        return p

    def cmp_jobinfo(result, info_list, job_type, actions):
        """
        Compare the output jobinfo with expected one

        :param result : the return from domjobinfo cmd
        :param info_list : an expected domjobinfo list
        :param job_type : an expected value for 'Job Type'
        :param actions : the job operation
        """
        logging.debug(result.stdout)
        out_list = result.stdout.strip().splitlines()
        out_dict = dict([x.split(':') for x in out_list])
        ret_cmp = set(out_dict.keys()) == set(info_list)
        if not ret_cmp:
            if set(info_list) - set(out_dict.keys()):
                test.fail("Missing expected items in domjobinfo output: %s"
                          % (set(info_list) - set(out_dict.keys())))
            else:
                new_cmp = set(out_dict.keys()) - set(info_list)
                known_item = {'Memory bandwidth'}
                # For running domjobinfo, 'Memory bandwidth' appears sometimes.
                if new_cmp != known_item or job_type == "Completed":
                    test.fail("New items appear: %s, pls modify script!", new_cmp)
        else:
            if out_dict["Job type"].strip() != job_type:
                test.fail("Expect %s Job type but got %s" %
                          (job_type, out_dict["Job type"].strip()))
            if out_dict["Operation"].strip() != actions.capitalize():
                test.fail("Expect %s Operation but got %s" %
                          (actions.capitalize(), out_dict["Operation"].strip()))

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    start_vm = params.get("start_vm")
    pre_vm_state = params.get("pre_vm_state", "start")
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
    actions = params.get("domjobinfo_action", "dump")
    act_opt = params.get("dump_opt", "")
    vm_ref = params.get("domjobinfo_vm_ref")
    status_error = params.get("status_error", "no")
    libvirtd = params.get("libvirtd", "on")
    # Use tmp_pipe to act as target file for job operation in subprocess,
    # such as vm.dump, vm.save, etc.
    tmp_pipe = os.path.join(data_dir.get_tmp_dir(), "domjobinfo.fifo")

    # Build job action
    action = ' '.join([actions, act_opt])
    if actions == "managedsave":
        tmp_pipe = '/var/lib/libvirt/qemu/save/%s.save' % vm.name

    # Expected domjobinfo list
    info_list = ["Job type", "Time elapsed",
                 "Data processed", "Data remaining", "Data total",
                 "Memory processed", "Memory remaining",
                 "Memory total", "Dirty rate",
                 "Iteration", "Constant pages", "Normal pages",
                 "Normal data", "Expected downtime", "Setup time"]
    if libvirt_version.version_compare(3, 2, 0):
        info_list.insert(1, "Operation")
        if libvirt_version.version_compare(3, 9, 0):
            info_list.insert(info_list.index("Dirty rate")+1, "Page size")
            if libvirt_version.version_compare(5, 0, 0):
                info_list.insert(info_list.index("Iteration")+1, "Postcopy requests")
    logging.debug("The expected info_list for running job is %s", info_list)

    # run test case
    if vm_ref == "id":
        vm_ref = domid
    elif vm_ref == "hex_id":
        vm_ref = hex(int(domid))
    elif vm_ref == "name":
        vm_ref = "%s %s" % (vm_name, params.get("domjobinfo_extra"))
    elif vm_ref == "uuid":
        vm_ref = domuuid
    elif 'invalid' in vm_ref:
        vm_ref = params.get(vm_ref)

    # Get the subprocess of VM.
    # The command's effect is to get domjobinfo of running domain job.
    # So before do "domjobinfo", we must create a job on the domain.
    process = None
    if start_vm == "yes" and status_error == "no":
        if os.path.exists(tmp_pipe):
            os.unlink(tmp_pipe)
        os.mkfifo(tmp_pipe)

        # Target file param is not needed for managedsave operation
        if action == "managedsave ":
            process = get_subprocess(action, vm_name, "", None)
        else:
            process = get_subprocess(action, vm_name, tmp_pipe, None)

        f = open(tmp_pipe, 'rb')
        dummy = f.read(1024 * 1024).decode(locale.getpreferredencoding(), 'ignore')

    if libvirtd == "off":
        utils_libvirtd.libvirtd_stop()

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

    # Get domjobinfo while job is running
    ret = virsh.domjobinfo(vm_ref, ignore_status=True, debug=True)
    status = ret.exit_status

    # Clear process env
    if process and f:
        dummy = f.read()
        f.close()

        try:
            os.unlink(tmp_pipe)
        except OSError as detail:
            logging.info("Can't remove %s: %s", tmp_pipe, detail)

    if process:
        if process.poll():
            try:
                process.kill()
            except OSError:
                pass

    # Get completed domjobinfo
    if status_error == "no":
        time.sleep(5)
        if act_opt != "--live" and vm_ref == domid:
            # use vm_name but not id since domain shutoff
            vm_ref = vm_name
        vm_ref = "%s --completed" % vm_ref
        ret_cmplt = virsh.domjobinfo(vm_ref, ignore_status=True, debug=True)
        status_cmplt = ret_cmplt.exit_status

    # Recover the environment.
    if actions == "managedsave":
        virsh.managedsave_remove(vm_name, ignore_status=True)
    if pre_vm_state == "suspend":
        vm.resume()
    if libvirtd == "off":
        utils_libvirtd.libvirtd_start()

    # Check status_error
    if status_error == "yes":
        if status == 0:
            if libvirtd == "off" and libvirt_version.version_compare(5, 6, 0):
                logging.info("From libvirt 5.6.0 libvirtd is restarted "
                             "and command should succeed")
            else:
                test.fail("Run successfully with wrong command!")
    elif status_error == "no":
        if status != 0 or status_cmplt != 0:
            test.fail("Run failed with right command")

    # if libvirtd wasn't running the jobinfo is exepected to be empty
    if status_error == "no" and not libvirtd == "off":
        # The 'managedsave' Operation will be shown as 'Save' in domjobinfo
        if actions == "managedsave":
            actions = "save"
        # Check output of "virsh domjobinfo"
        cmp_jobinfo(ret, info_list, "Unbounded", actions)
        # Check output of "virsh domjobinfo --completed"
        info_list.insert(info_list.index("Memory total")+1, "Memory bandwidth")
        info_list[info_list.index("Expected downtime")] = "Total downtime"
        logging.debug("The expected info_list for completed job is %s", info_list)
        cmp_jobinfo(ret_cmplt, info_list, "Completed", actions)
