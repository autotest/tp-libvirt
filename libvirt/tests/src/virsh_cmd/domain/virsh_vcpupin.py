import logging
import re
import random

from avocado.utils import process
from avocado.utils import cpu as cpuutils

from virttest import virsh
from virttest import cpu
from virttest import utils_misc
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Test the command virsh vcpupin

    (1) Get the host and guest cpu count
    (2) Call virsh vcpupin for each vcpu with pinning of each cpu
    (3) Check whether the virsh vcpupin has pinned the respective vcpu to cpu
    """

    def affinity_from_vcpuinfo(vm_name, vcpu):
        """
        This function returns list of the vcpu's affinity from
        virsh vcpuinfo output

        :param vm_name: VM Name to operate on
        :param vcpu: vcpu number for which the affinity is required
        """

        output = virsh.vcpuinfo(vm_name).stdout.rstrip()
        affinity = re.findall('CPU Affinity: +[-y]+', output)
        total_affinity = affinity[int(vcpu)].split()[-1].strip()
        actual_affinity = list(total_affinity)
        return actual_affinity

    def affinity_from_vcpupin(vm_name, vcpu):
        """
        This function returns list of vcpu's affinity from vcpupin output

        :param vm_name: VM Name
        :param vcpu: VM cpu pid
        :return : list of affinity to vcpus
        """
        total_cpu = process.run("ls -d /sys/devices/system/cpu/cpu[0-9]* |wc -l", shell=True).stdout_text.strip()
        vcpus_affinity = {}
        output = virsh.vcpupin(vm_name).stdout
        for item in output.split('\n')[2:-2]:
            item = item.strip()
            split_key = ' '
            if ':' in item:
                split_key = ':'
            vcpus_affinity[item.split(split_key)[0].strip()] = item.split(split_key)[-1].strip()
        return cpu.cpus_string_to_affinity_list(
            vcpus_affinity[str(vcpu)], int(total_cpu))

    def check_vcpupin(vm_name, vcpu, cpu_list, pid, vcpu_pid):
        """
        This function checks the actual and the expected affinity of given vcpu
        and raises error if not matchs

        :param vm_name: VM Name to operate on
        :param vcpu: vcpu number for which the affinity is required
        :param cpu: cpu details for the affinity
        :param pid: VM pid
        :param vcpu: VM cpu pid
        """

        total_cpu = process.run("ls -d /sys/devices/system/cpu/cpu[0-9]* |wc -l", shell=True).stdout_text.strip()
        logging.debug("Debug: cpulist %s", cpu_list)
        expected_output = cpu.cpus_string_to_affinity_list(
            cpu_list,
            int(total_cpu))
        logging.debug("Expected affinity: %s", expected_output)

        # Check for affinity value from vcpuinfo output
        actual_output = affinity_from_vcpuinfo(vm_name, vcpu)
        logging.debug("Actual affinity in vcpuinfo output: %s", actual_output)
        if expected_output == actual_output:
            logging.info("successfully pinned cpu_list: %s --> vcpu: %s",
                         cpu_list, vcpu)
        else:
            test.fail("Cpu pinning details not updated properly in"
                      " virsh vcpuinfo command output")

        # Check for affinity value from vcpupin output
        actual_output_vcpupin = affinity_from_vcpupin(vm_name, vcpu)
        logging.debug("Actual affinity in vcpupin output: %s", actual_output_vcpupin)
        if expected_output == actual_output_vcpupin:
            logging.info("successfully pinned cpu_list: %s --> vcpu: %s",
                         cpu_list, vcpu)
        else:
            test.fail("Cpu pinning details not updated properly in"
                      " virsh vcpupin command output")

        if pid is None:
            return
        # Get the actual cpu affinity value in the proc entry
        output = cpu.cpu_allowed_list_by_task(pid, vcpu_pid)
        actual_output_proc = cpu.cpus_string_to_affinity_list(
            output,
            int(total_cpu))
        logging.debug("Actual affinity in guest proc: %s", actual_output_proc)
        if expected_output == actual_output_proc:
            logging.info("successfully pinned vcpu: %s --> cpu: %s"
                         " in respective proc entry", vcpu, cpu_list)
        else:
            test.fail("Cpu pinning details are not "
                      "updated properly in /proc/"
                      "%s/task/%s/status" % (pid, vcpu_pid))

    def run_and_check_vcpupin(vm, vm_ref, vcpu, cpu_list, options):
        """
        Run the vcpupin command and then check the result.
        """
        if vm_ref == "name":
            vm_ref = vm.name
        elif vm_ref == "uuid":
            vm_ref = vm.get_uuid()
        # Execute virsh vcpupin command.
        cmdResult = virsh.vcpupin(vm_ref, vcpu, cpu_list, options, debug=True)
        if cmdResult.exit_status:
            if not status_error:
                # Command fail and it is positive case.
                test.fail(cmdResult)
            else:
                # Command fail and it is negative case.
                return
        else:
            if status_error:
                # Command success and it is negative case.
                test.fail(cmdResult)
            else:
                # Command success and it is positive case.
                # "--config" will take effect after VM destroyed.
                pid = None
                vcpu_pid = None
                if options == "--config":
                    virsh.destroy(vm.name)
                else:
                    pid = vm.get_pid()
                    logging.debug("vcpus_pid: %s", vm.get_vcpus_pid())
                    vcpu_pid = vm.get_vcpus_pid()[vcpu]
                # Check the result of vcpupin command.
                check_vcpupin(vm.name, vcpu, cpu_list, pid, vcpu_pid)

    def offline_pin_and_check(vm, vcpu, cpu_list):
        """
        Edit domain xml to pin vcpu and check the result.
        """
        cputune = vm_xml.VMCPUTuneXML()
        cputune.vcpupins = [{'vcpu': str(vcpu), 'cpuset': cpu_list}]
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm.name)
        vmxml.cputune = cputune
        vmxml.sync()
        utils_misc.wait_for(lambda: vm.state() == "shut off", 10)
        cmdResult = virsh.start(vm.name, debug=True)
        libvirt.check_exit_status(cmdResult, status_error)
        pid = vm.get_pid()
        vcpu_pid = vm.get_vcpus_pid()[vcpu]
        check_vcpupin(vm.name, vcpu, cpu_list, pid, vcpu_pid)

    if not virsh.has_help_command('vcpucount'):
        test.cancel("This version of libvirt doesn't"
                    " support this test")
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    # Get the variables for vcpupin command.
    vm_ref = params.get("vcpupin_vm_ref", "name")
    options = params.get("vcpupin_options", "--current")
    cpu_list = params.get("vcpupin_cpu_list", "x")
    cpu_itrs = int(params.get("vcpupin_cpu_itrs", "20"))
    start_vm = ("yes" == params.get("start_vm", "yes"))
    vcpupin_initial = ("yes" == params.get("vcpupin_initial", "no"))

    # Get status of this case.
    status_error = ("yes" == params.get("status_error", "no"))

    # Edit domain xml to pin vcpus
    offline_pin = ("yes" == params.get("offline_pin", "no"))

    # Backup for recovery.
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    vmxml_backup = vmxml.copy()

    try:
        # Remove cputune settings that will affect affinity info
        if vcpupin_initial and vmxml.xmltreefile.find('cputune'):
            vmxml.del_cputune()
            vmxml.sync()

        if (start_vm or vcpupin_initial) and vm.state() == "shut off":
            cmdResult = virsh.start(vm.name, debug=True)
            libvirt.check_exit_status(cmdResult, status_error)

        # Get the guest vcpu count
        if offline_pin:
            vcpucount_option = "--config --active"
        else:
            vcpucount_option = "--live --active"
        guest_vcpu_count = virsh.vcpucount(vm_name,
                                           vcpucount_option).stdout.strip()

        # Find the alive cpus list
        cpus_list = list(map(str, cpuutils.cpu_online_list()))
        logging.info("Active cpus in host are %s", cpus_list)

        # If the cpus_list has too many cpus, then only test the
        # cpus of the head, middle and tail part
        lenght = len(cpus_list)
        if lenght > 30:
            cpus_list = cpus_list[:10] + \
                        cpus_list[lenght//2 - 5: lenght//2 + 5] + \
                        cpus_list[lenght - 10:]
            logging.info('Will run test on cpus: %s', cpus_list)

        # Control multi domain vcpu affinity
        multi_dom = ("yes" == params.get("multi_dom_pin", "no"))
        vm2 = None
        # Before doing any vcpupin actions, lets check whether
        # initial pinning state is fine
        if vcpupin_initial:
            pid = vm.get_pid()
            logging.debug("vcpus_pid: %s vcpu count: %s", vm.get_vcpus_pid(), guest_vcpu_count)
            for vcpu in range(int(guest_vcpu_count)):
                vcpu_pid = vm.get_vcpus_pid()[vcpu]
                # Check the result of vcpupin command.
                check_vcpupin(vm.name, vcpu, str(','.join(list(map(str, cpuutils.cpu_online_list())))), pid, vcpu_pid)
            return

        if multi_dom:
            vm_names = params.get("vms").split()
            if len(vm_names) > 1:
                vm2 = env.get_vm(vm_names[1])
            else:
                test.error("Need more than one domains")
            if not vm2:
                test.cancel("No %s find" % vm_names[1])
            vm2.destroy()
            vm2xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm2.name)
            vm2xml_backup = vm2xml.copy()
            # Make sure vm2 has the same cpu numbers with vm
            vm2xml.set_vm_vcpus(vm2.name, int(guest_vcpu_count), guest_vcpu_count)
            if start_vm:
                vm2.start()

        # Run cases when guest is shutoff.
        if not offline_pin:
            if vm.is_dead() and not start_vm:
                run_and_check_vcpupin(vm, vm_ref, 0, 0, "")
                return
        # Get the host cpu count
        host_online_cpu_count = len(cpus_list)
        online_cpu_max = max(map(int, cpus_list))
        host_cpu_count = cpuutils.total_cpus_count()
        cpu_max = int(host_cpu_count) - 1
        if (host_online_cpu_count < 2) and (not cpu_list == "x"):
            test.cancel("We need more cpus on host in this "
                        "case for the cpu_list=%s. But "
                        "current number of cpu on host is %s."
                        % (cpu_list, host_online_cpu_count))

        # Run test case
        for vcpu in range(int(guest_vcpu_count)):
            if cpu_list == "x":
                # Lets make 2 times cpu_itrs value as threshold
                # for guest vcpu iterations as there are possibility
                # the iterations will grow bigger number causing test
                # to run for a very long time and based on assumption
                # this let us recalculate cpu_itrs value with least value as 2
                if (int(guest_vcpu_count) * host_online_cpu_count) > (2 * cpu_itrs * cpu_itrs):
                    if int(guest_vcpu_count) > 2 * cpu_itrs:
                        cpu_itrs = (2 * cpu_itrs * cpu_itrs)/int(guest_vcpu_count)
                        if cpu_itrs == 0:
                            cpu_itrs = 2
                for _ in range(cpu_itrs):
                    cpuid = random.choice(cpus_list)
                    left_cpus = "0-%s,^%s" % (online_cpu_max, cpuid)
                    if offline_pin:
                        offline_pin_and_check(vm, vcpu, str(cpuid))
                        if multi_dom:
                            offline_pin_and_check(vm2, vcpu, left_cpus)
                    else:
                        run_and_check_vcpupin(vm, vm_ref, vcpu, str(cpuid),
                                              options)
                        if multi_dom:
                            run_and_check_vcpupin(vm2, "name", vcpu, left_cpus,
                                                  options)
            else:
                if cpu_list == "x-y":
                    cpus = "0-%s" % online_cpu_max
                elif cpu_list == "x,y":
                    cpus = ','.join(random.sample(cpus_list, 2))
                    logging.info(cpus)
                elif cpu_list == "x-y,^z":
                    cpus = "0-%s,^%s" % (online_cpu_max, online_cpu_max)
                elif cpu_list == "r":
                    cpus = "r"
                elif cpu_list == "-1":
                    cpus = "-1"
                elif cpu_list == "out_of_max":
                    cpus = str(cpu_max + 1)
                else:
                    test.cancel("Cpu_list=%s is not recognized."
                                % cpu_list)
                if offline_pin:
                    offline_pin_and_check(vm, vcpu, cpus)
                else:
                    run_and_check_vcpupin(vm, vm_ref, vcpu, cpus, options)
    finally:
        # Recover xml of vm.
        vmxml_backup.sync()
        if vm2:
            vm2xml_backup.sync()
