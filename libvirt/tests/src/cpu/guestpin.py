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
from virttest import utils_package
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
            if condn == "avocado_test":
                testlist = utils_test.get_avocadotestlist(params)
                bt = utils_test.run_avocado_bg(vm, params, test, testlist)
                if not bt:
                    test.cancel("guest stress failed to start")
                # Allow stress to start
                time.sleep(condn_sleep_sec)
                return bt
            elif condn == "stress":
                session = vm.wait_for_login()
                if not utils_package.package_install("gcc", session):
                    test.fail("Failed to install gcc in guest")
                session.close()
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
            elif condn == "avocado_test":
                guestbt.join()
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
            cpustats = {}
            result = virsh.emulatorpin(vm_name, cpus_list[-1], "config",
                                       debug=True)
            libvirt.check_exit_status(result)
            result = virsh.vcpupin(vm_name, "0", cpus_list[0], "--config",
                                   ignore_status=True, debug=True)
            libvirt.check_exit_status(result)
        try:
            vm.start()
        except virt_vm.VMStartError as detail:
            test.fail("%s" % detail)

        cpucount = vm.get_cpu_count()
        if cpucount != current_vcpu:
            test.fail("Incorrect initial guest vcpu\nExpected:%s Actual:%s" %
                      (cpucount, current_vcpu))

        if config_pin:
            cpustats = cpu.get_cpustats(vm)
            if not cpustats:
                test.fail("cpu stats command failed to run")

            logging.debug("Check cpustats for emulatorpinned cpu")
            if cpustats[cpus_list[-1]][0] > 0:
                fail = True
                logging.error("Non zero vcputime even with no vcpu pinned")
            if cpustats[cpus_list[-1]][1] == 0:
                fail = True
                logging.error("emulatortime should be positive as it is pinned")

            logging.debug("Check cpustats for vcpupinned cpu")
            if cpustats[cpus_list[0]][0] == 0:
                fail = True
                logging.error("vcputime should be positive as vcpu it is pinned")
            if cpustats[cpus_list[0]][1] > 0:
                fail = True
                logging.error("Non zero emulatortime even with emulator unpinned")

            logging.debug("Check cpustats for non-pinned cpus")
            for index in cpus_list[1:-1]:
                if cpustats[index][2] > 0:
                    fail = True
                    logging.error("Non zero cputime even with no vcpu,emualtor pinned")

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
                    if cpustats[hostcpu][1] > 0:
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
