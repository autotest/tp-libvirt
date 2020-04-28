import logging
import os
import random
import time

from avocado.utils import process
from avocado.utils import cpu as cpuutil

from virttest import cpu
from virttest import virsh
from virttest import virt_vm
from virttest import data_dir
from virttest import utils_test
from virttest import libvirt_xml
from virttest.utils_test import libvirt
from virttest.staging import utils_cgroup


def run(test, params, env):
    """
    Different vcpupin scenario tests
    1) prepare the guest with given topology, memory and if any devices
    2) Start and login to the guest, check for cpu, memory
    3) Do different combinations of vcpupin and in parallel run stress
       if given
    4) Do a optional step based on config
    5) Check guest and host functional

    :param test: QEMU test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """

    def set_condition(vm_name, condn, reset=False, guestbt=None):
        """
        Set domain to given state or reset it.
        """
        bt = None
        if not reset:
            if condn == "avocadotest":
                bt = utils_test.run_avocado_bg(vm, params, test)
                if not bt:
                    test.cancel("guest stress failed to start")
                # Allow stress to start
                time.sleep(condn_sleep_sec)
                return bt
            elif condn == "stress":
                utils_test.load_stress("stress_in_vms", params=params, vms=[vm])
            elif condn in ["save", "managedsave"]:
                # No action
                pass
            elif condn == "suspend":
                result = virsh.suspend(vm_name, ignore_status=True, debug=True)
                libvirt.check_exit_status(result)
            elif condn == "hotplug":
                result = virsh.setvcpus(vm_name, max_vcpu, "--live",
                                        ignore_status=True, debug=True)
                libvirt.check_exit_status(result)
                exp_vcpu = {'max_config': max_vcpu, 'max_live': max_vcpu,
                            'cur_config': current_vcpu, 'cur_live': max_vcpu,
                            'guest_live': max_vcpu}
                result = cpu.check_vcpu_value(vm, exp_vcpu,
                                              option="--live")
            elif condn == "host_smt":
                if cpuutil.get_cpu_vendor_name() == 'power9':
                    result = process.run("ppc64_cpu --smt=4", shell=True)
                else:
                    test.cancel("Host SMT changes not allowed during guest live")
            else:
                logging.debug("No operation for the domain")

        else:
            if condn == "save":
                save_file = os.path.join(data_dir.get_tmp_dir(), vm_name + ".save")
                result = virsh.save(vm_name, save_file,
                                    ignore_status=True, debug=True)
                libvirt.check_exit_status(result)
                time.sleep(condn_sleep_sec)
                if os.path.exists(save_file):
                    result = virsh.restore(save_file, ignore_status=True,
                                           debug=True)
                    libvirt.check_exit_status(result)
                    os.remove(save_file)
                else:
                    test.error("No save file for domain restore")
            elif condn == "managedsave":
                result = virsh.managedsave(vm_name,
                                           ignore_status=True, debug=True)
                libvirt.check_exit_status(result)
                time.sleep(condn_sleep_sec)
                result = virsh.start(vm_name, ignore_status=True, debug=True)
                libvirt.check_exit_status(result)
            elif condn == "suspend":
                result = virsh.resume(vm_name, ignore_status=True, debug=True)
                libvirt.check_exit_status(result)
            elif condn == "avocadotest":
                guestbt.join(ignore_status=True)
            elif condn == "stress":
                utils_test.unload_stress("stress_in_vms", params=params, vms=[vm])
            elif condn == "hotplug":
                result = virsh.setvcpus(vm_name, current_vcpu, "--live",
                                        ignore_status=True, debug=True)
                libvirt.check_exit_status(result)
                exp_vcpu = {'max_config': max_vcpu, 'max_live': current_vcpu,
                            'cur_config': current_vcpu, 'cur_live': current_vcpu,
                            'guest_live': current_vcpu}
                result = cpu.check_vcpu_value(vm, exp_vcpu,
                                              option="--live")
            elif condn == "host_smt":
                result = process.run("ppc64_cpu --smt=2", shell=True)
                # Change back the host smt
                result = process.run("ppc64_cpu --smt=4", shell=True)
                # Work around due to known cgroup issue after cpu hot(un)plug
                # sequence
                root_cpuset_path = utils_cgroup.get_cgroup_mountpoint("cpuset")
                machine_cpuset_paths = []
                if os.path.isdir(os.path.join(root_cpuset_path,
                                              "machine.slice")):
                    machine_cpuset_paths.append(os.path.join(root_cpuset_path,
                                                             "machine.slice"))
                if os.path.isdir(os.path.join(root_cpuset_path, "machine")):
                    machine_cpuset_paths.append(os.path.join(root_cpuset_path,
                                                             "machine"))
                if not machine_cpuset_paths:
                    logging.warning("cgroup cpuset might not recover properly "
                                    "for guests after host smt changes, "
                                    "restore it manually")
                root_cpuset_cpus = os.path.join(root_cpuset_path, "cpuset.cpus")
                for path in machine_cpuset_paths:
                    machine_cpuset_cpus = os.path.join(path, "cpuset.cpus")
                    # check if file content differs
                    cmd = "diff %s %s" % (root_cpuset_cpus,
                                          machine_cpuset_cpus)
                    if process.system(cmd, verbose=True, ignore_status=True):
                        cmd = "cp %s %s" % (root_cpuset_cpus,
                                            machine_cpuset_cpus)
                        process.system(cmd, verbose=True)

            else:
                logging.debug("No need recover the domain")
        return bt

    vm_name = params.get("main_vm")
    max_vcpu = int(params.get("max_vcpu", 2))
    current_vcpu = int(params.get("current_vcpu", 1))
    vm_cores = int(params.get("limit_vcpu_cores", 2))
    vm_threads = int(params.get("limit_vcpu_threads", 1))
    vm_sockets = int(params.get("limit_vcpu_sockets", 1))
    vm = env.get_vm(vm_name)
    condition = params.get("condn", "")
    condn_sleep_sec = int(params.get("condn_sleep_sec", 30))
    cpustats_settle_time = int(params.get("cpustats_settle_time", 2))
    pintype = params.get("pintype", "random")
    emulatorpin = "yes" == params.get("emulatorpin", "no")
    config_pin = "yes" == params.get("config_pin", "no")
    iterations = int(params.get("itr", 1))
    vmxml = libvirt_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    org_xml = vmxml.copy()
    fail = False
    # Destroy the vm
    vm.destroy()
    try:
        cpus_list = cpuutil.cpu_online_list()
        if len(cpus_list) < 2:
            test.cancel("Need minimum two online host cpus")
        # Set vcpu and topology
        libvirt_xml.VMXML.set_vm_vcpus(vm_name, max_vcpu, current_vcpu,
                                       vm_sockets, vm_cores, vm_threads)
        if config_pin:
            cpustats_itr1 = {}
            cpustats_itr2 = {}
            conf_emupin_cpu = cpus_list[-1]
            conf_vcpupin_cpu = cpus_list[0]
            result = virsh.emulatorpin(vm_name, conf_emupin_cpu, "config",
                                       debug=True)
            libvirt.check_exit_status(result)
            result = virsh.vcpupin(vm_name, "0", conf_vcpupin_cpu, "--config",
                                   ignore_status=True, debug=True)
            libvirt.check_exit_status(result)

        try:
            vm.start(autoconsole=False)
        except virt_vm.VMStartError as detail:
            test.fail("%s" % detail)

        if config_pin:
            # https://www.redhat.com/archives/libvir-list/2020-April/msg00543.html
            # Let's wait for 2 seconds initially and to be get more stable
            # value wait for another 2 seconds for second iteration.
            # Boot time of guest varies with increase in guest memory and
            # host resources, So lets wait for 2 seconds, though documentation
            # says 200 microseconds is enough, to accommodate all environments.
            # by-default, cpustats_settle_time is set to 2 seconds due to above
            # explanation, anyways user free to modify based on their environment.
            time.sleep(cpustats_settle_time)
            cpustats_itr1 = cpu.get_cpustats(vm)
            if not cpustats_itr1:
                test.fail("cpu-stats command failed to run")
            time.sleep(cpustats_settle_time)
            cpustats_itr2 = cpu.get_cpustats(vm)
            non_pinned_vcputime_itr1 = 0.0
            non_pinned_vcputime_itr2 = 0.0
            non_pinned_emulatortime_itr1 = 0.0
            non_pinned_emulatortime_itr2 = 0.0
            for i in cpus_list:
                if not i == conf_vcpupin_cpu:
                    non_pinned_vcputime_itr1 += cpustats_itr1[i][0]
                    non_pinned_vcputime_itr2 += cpustats_itr2[i][0]
                if not i == conf_emupin_cpu:
                    non_pinned_emulatortime_itr1 += cpustats_itr1[i][1]
                    non_pinned_emulatortime_itr2 += cpustats_itr2[i][1]
            logging.debug("Check cpustats for non-pinned cpus")
            if non_pinned_vcputime_itr2 > non_pinned_vcputime_itr1:
                fail = True
                logging.error("Non zero vcputime even with no vcpu pinned")
            if non_pinned_emulatortime_itr2 > non_pinned_emulatortime_itr1:
                fail = True
                logging.error("Non zero emulatortime even with emulator unpinned")

            logging.debug("Check cpustats for emulatorpinned cpu")
            if cpustats_itr2[conf_emupin_cpu][1] == 0:
                fail = True
                logging.error("emulatortime should be positive as it is pinned")

            logging.debug("Check cpustats for vcpupinned cpu")
            if cpustats_itr2[conf_vcpupin_cpu][0] == 0:
                fail = True
                logging.error("vcputime should be positive as vcpu it is pinned")

        vm.create_serial_console()
        cpucount = vm.get_cpu_count()
        if cpucount != current_vcpu:
            test.fail("Incorrect initial guest vcpu\nExpected:%s Actual:%s" %
                      (cpucount, current_vcpu))

        if condition:
            condn_result = set_condition(vm_name, condition)

        # Action:
        for _ in range(iterations):
            if emulatorpin:
                # To make sure cpu to be offline during host_smt
                hostcpu = cpus_list[-1]
                result = virsh.emulatorpin(vm_name, hostcpu, debug=True)
                libvirt.check_exit_status(result)
                cpustats = cpu.get_cpustats(vm, hostcpu)
                logging.debug("hostcpu:%s vcputime: %s emulatortime: "
                              "%s cputime: %s", hostcpu, cpustats[hostcpu][0],
                              cpustats[hostcpu][1], cpustats[hostcpu][2])
            for vcpu in range(max_vcpu):
                if pintype == "random":
                    hostcpu = random.choice(cpus_list[:-1])
                if pintype == "sequential":
                    hostcpu = cpus_list[vcpu % len(cpus_list[:-1])]
                cpustats_before_pin = cpu.get_cpustats(vm, hostcpu)
                result = virsh.vcpupin(vm_name, vcpu, hostcpu,
                                       ignore_status=True, debug=True)
                libvirt.check_exit_status(result)
                cpustats = cpu.get_cpustats(vm, hostcpu)
                logging.debug("hostcpu:%s vcputime: %s emulatortime: "
                              "%s cputime: %s", hostcpu, cpustats[hostcpu][0],
                              cpustats[hostcpu][1], cpustats[hostcpu][2])
                if config_pin:
                    if cpustats[hostcpu][0] == 0:
                        fail = True
                        logging.error("vcputime should be positive as vcpu is pinned")
                    if cpustats[hostcpu][1] > cpustats_before_pin[hostcpu][1]:
                        fail = True
                        logging.error("Non zero emulatortime even with emulator unpinned")
        if condition:
            set_condition(vm_name, condition, reset=True, guestbt=condn_result)

        # Check for guest functional
        cpucount = vm.get_cpu_count()
        if cpucount != current_vcpu:
            test.fail("Incorrect final guest vcpu\nExpected:%s Actual:%s" %
                      (cpucount, current_vcpu))
    finally:
        if fail:
            test.fail("Consult previous errors")
        org_xml.sync()
