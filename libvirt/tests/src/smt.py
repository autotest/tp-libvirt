import time
import logging
import re

from avocado.utils import process
from avocado.utils import astring

from virttest.libvirt_xml import vm_xml
from virttest import utils_package
from virttest import virt_vm


def run(test, params, env):
    """
    libvirt smt test:
    1) prepare the guest with given topology
    2) Start and login to the guest
    3) Check for ppc64_cpu --smt and smt should be on
    4) ppc64_cpu --smt=off and smt should be off
    5) ppc64_cpu --smt=on and smt should be on
    6) Check for core present using  ppc64_cpu
    7) Check for online core using ppc64_cpu
    8) Check for lscpu for thread, core, socket info updated properly
    9) Change the number of cores and check in lscpu

    :param test: QEMU test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    error_count = 0

    def smt_check(vm, cmd, output, extra=None, ignorestatus=False):
        """
        Run and check SMT command inside guest

        :param vm: VM object
        :param cmd: Given smt command
        :param output: Expected output
        :param extra: Extra output to be added
        :param ignorestatus: True or False to ignore status
        :return: error count
        """
        err_count = 0
        session = vm.wait_for_login()
        actual_output = session.cmd_output(cmd).strip()
        return_output = session.cmd_output('echo $?').strip()
        if extra:
            expected_output = output + extra
        else:
            expected_output = output
        if expected_output != actual_output:
            logging.error("Command: %s failed\nActual output: %s\nExpected "
                          "output: %s", cmd, actual_output, expected_output)
            if int(return_output) == 0 and not ignorestatus:
                logging.error("Command: %s returned zero"
                              "\n Expecting a non zero number", cmd)
            err_count = 1
        else:
            if int(return_output) != 0 and not ignorestatus:
                logging.error("Command: %s returned non-zero"
                              "\n Expecting zero", cmd)
                err_count += 1
            else:
                logging.debug("Command: %s ran successfully", cmd)
        session.close()
        return err_count

    def cpus_info(vm, env="guest"):
        """
        To get host cores, threads, sockets in the system

        :param vm: VM object
        :param env: guest or host
        :return: cpu sockets, cores, threads info as list
        """
        if "guest" in env:
            session = vm.wait_for_login()
            output = session.cmd_output("lscpu")
        else:
            output = astring.to_text(process.system_output("lscpu", shell=True))
        no_cpus = int(re.findall('CPU\(s\):\s*(\d+)', str(output))[0])
        no_threads = int(re.findall('Thread\(s\)\sper\score:\s*(\d+)', str(output))[0])
        no_cores = int(re.findall('Core\(s\)\sper\ssocket:\s*(\d+)', str(output))[0])
        no_sockets = int(re.findall('Socket\(s\):\s*(\d+)', str(output))[0])
        cpu_info = [no_cpus, no_threads, no_cores, no_sockets]
        if "guest" in env:
            session.close()
        return cpu_info

    vm_name = params.get("main_vm")
    smt_chk_cmd = params.get("smt_chk_cmd", "ppc64_cpu --smt")
    smt_on_cmd = params.get("smt_on_cmd", "ppc64_cpu --smt=on")
    smt_off_cmd = params.get("smt_off_cmd", "ppc64_cpu --smt=off")
    smt_core_pst_cmd = params.get("smt_core_present_cmd",
                                  "ppc64_cpu --cores-present")
    smt_core_on_cmd = params.get("smt_core_on_cmd", "ppc64_cpu --cores-on")
    smt_chk_on_output = params.get("smt_chk_on_output", "SMT is on")
    smt_chk_off_output = params.get("smt_chk_off_output", "SMT is off")
    smt_core_pst_output = params.get("smt_core_pst_output",
                                     "Number of cores present =")
    smt_core_on_output = params.get("smt_core_on_output",
                                    "Number of cores online =")
    smt_threads_per_core_cmd = params.get("smt_threads_per_core_cmd",
                                          "ppc64_cpu --threads-per-core")
    smt_threads_per_core_output = params.get("smt_threads_per_core_ouput",
                                             "Threads per core:")
    status_error = params.get("status_error", "no") == "yes"
    ignore_status = params.get("ignore_status", "no") == "yes"

    smt_number = params.get("smt_number", None)
    max_vcpu = current_vcpu = int(params.get("smt_smp", 8))
    vm_cores = int(params.get("smt_vcpu_cores", 8))
    vm_threads = int(params.get("smt_vcpu_threads", 1))
    vm_sockets = int(params.get("smt_vcpu_sockets", 1))
    vm = env.get_vm(vm_name)

    output = astring.to_text(process.system_output(smt_threads_per_core_cmd, shell=True))
    try:
        host_threads = int(re.findall('Threads per core:\s+(\d+)', output)[0])
    except Exception as err:
        test.cancel("Unable to get the host threads\n %s" % err)

    logging.info("Guest: cores:%d, threads:%d, sockets:%d", vm_cores,
                 vm_threads, vm_sockets)
    try:
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        org_xml = vmxml.copy()
        vm.destroy()
        # Initial Setup of vm
        vmxml.set_vm_vcpus(vm_name, max_vcpu, current_vcpu,
                           vm_sockets, vm_cores, vm_threads,
                           add_topology=True)
        try:
            vm.start()
            if status_error:
                test.fail("VM Started with invalid thread %s" % vm_threads)
        except virt_vm.VMStartError as detail:
            if not status_error:
                test.fail("VM failed to start %s" % detail)

        if not status_error:
            # try installing powerpc-utils in guest if not skip
            try:
                session = vm.wait_for_login()
                utils_package.package_install(["powerpc-utils"], session, 360)
                session.close()
            except Exception as err:
                test.cancel("Unable to install powerpc-utils package in guest\n %s" % err)
            # Changing the smt number
            if smt_number:
                smt_chk_cmd_mod = "%s=%s" % (smt_chk_cmd, smt_number)
                error_count += smt_check(vm, smt_chk_cmd_mod, "")

            guest_cpu_details = cpus_info(vm)
            # Step 10: Check for threads, cores, sockets
            if vm_cores != guest_cpu_details[2]:
                logging.error("Number of cores mismatch:\nExpected number of "
                              "cores: %s\nActual number of cores: %s",
                              vm_cores, guest_cpu_details[2])
                error_count += 1
            if smt_number:
                threads = int(smt_number)
            else:
                threads = vm_threads
            if threads != guest_cpu_details[1]:
                logging.error("Number of threads mismatch:\nExpected number of "
                              "threads: %s\nActual number of threads: %s",
                              threads, guest_cpu_details[1])
                error_count += 1
            if vm_sockets != guest_cpu_details[3]:
                logging.error("Number of sockets mismatch:\nExpected number of "
                              "sockets: %s\nActual number of sockets: %s",
                              vm_sockets, guest_cpu_details[3])
                error_count += 1

            error_count += smt_check(vm, smt_chk_cmd, smt_chk_on_output,
                                     ignorestatus=ignore_status)
            session = vm.wait_for_login()
            session.cmd_output(smt_off_cmd)
            session.close()
            error_count += smt_check(vm, smt_chk_cmd, smt_chk_off_output,
                                     ignorestatus=ignore_status)
            cores = vm_cores * vm_sockets
            extra = " %s" % cores
            error_count += smt_check(vm, smt_core_pst_cmd,
                                     smt_core_pst_output, extra)
            extra = " %s" % cores
            error_count += smt_check(vm, smt_core_on_cmd, smt_core_on_output, extra)
            extra = " %s" % vm_threads
            error_count += smt_check(vm, smt_threads_per_core_cmd,
                                     smt_threads_per_core_output, extra)

            # Changing the cores
            cores -= 1
            while cores > 1:
                smt_core_on_cmd_mod = "%s=%s" % (smt_core_on_cmd, cores)
                error_count += smt_check(vm, smt_core_on_cmd_mod, "")
                extra = " %s" % cores
                error_count += smt_check(vm, smt_core_on_cmd,
                                         smt_core_on_output, extra)
                guest_cpu_details = cpus_info(vm)
                if cores != (guest_cpu_details[3] * guest_cpu_details[2]):
                    logging.error("The core changes through command: %s not "
                                  "reflected in lscpu output", smt_core_on_cmd_mod)
                    error_count += 1
                cores -= 1
                # wait for sometime before next change of cores
                time.sleep(5)

            if error_count > 0:
                test.fail("The SMT feature has issue, please consult "
                          "previous errors more details")
    finally:
        org_xml.sync()
