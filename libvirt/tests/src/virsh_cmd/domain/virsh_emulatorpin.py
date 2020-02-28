import os
import logging
import random

from avocado.utils import cpu

from virttest.libvirt_xml import vm_xml
from virttest import utils_libvirtd, virsh
from virttest.cpu import cpus_parser
from virttest.staging import utils_cgroup
from virttest.virt_vm import VMStartError


def get_emulatorpin_from_cgroup(params, test):
    """
    Get a list of domain-specific per block stats from cgroup blkio controller.
    :params: the parameter dictionary
    """

    vm = params.get("vm")

    cpuset_path = \
        utils_cgroup.resolve_task_cgroup_path(vm.get_pid(), "cpuset")

    cpuset_file = os.path.join(cpuset_path, "cpuset.cpus")

    try:
        with open(cpuset_file, "rU") as f_emulatorpin_params:
            emulatorpin_params_from_cgroup = f_emulatorpin_params.readline()
        return emulatorpin_params_from_cgroup
    except IOError:
        test.error("Failed to get emulatorpin "
                   "params from %s" % cpuset_file)


def check_emulatorpin(params, test):
    """
    Check emulator affinity
    :params: the parameter dictionary
    """
    dicts = {}
    vm = params.get("vm")
    vm_name = params.get("main_vm")
    cpu_list = params.get("cpu_list")
    cgconfig = params.get("cgconfig", "on")
    options = params.get("emulatorpin_options")

    result = virsh.emulatorpin(vm_name, debug=True)
    cmd_output = result.stdout.strip().splitlines()
    logging.debug(cmd_output)
    # Parsing command output and putting them into python dictionary.
    for l in cmd_output[2:]:
        k, v = l.split(':')
        dicts[k.strip()] = v.strip()

    logging.debug(dicts)

    emulator_from_cmd = dicts['*']
    emulatorpin_from_xml = ""

    # To change a running guest with 'config' option, which will affect
    # next boot, if don't shutdown the guest, we need to run virsh dumpxml
    # with 'inactive' option to get guest XML changes.
    if options == "config" and vm and not vm.is_alive():
        emulatorpin_from_xml = \
            vm_xml.VMXML().new_from_dumpxml(vm_name, "--inactive").cputune.emulatorpin
    else:
        emulatorpin_from_xml = \
            vm_xml.VMXML().new_from_dumpxml(vm_name).cputune.emulatorpin

    # To get guest corresponding emulator/cpuset.cpus value
    # from cpuset controller of the cgroup.
    if cgconfig == "on" and vm and vm.is_alive():
        emulatorpin_from_cgroup = get_emulatorpin_from_cgroup(params, test)
        logging.debug("The emulatorpin value from "
                      "cgroup: %s", emulatorpin_from_cgroup)

    # To check specified cpulist value with virsh command output
    # and/or cpuset.cpus from cpuset controller of the cgroup.
    if cpu_list:
        if vm and vm.is_alive() and options != "config":
            if (cpu_list != cpus_parser(emulator_from_cmd)) or \
                    (cpu_list != cpus_parser(emulatorpin_from_cgroup)):
                logging.error("To expect emulatorpin %s: %s",
                              cpu_list, emulator_from_cmd)
                return False
        else:
            if cpu_list != cpus_parser(emulatorpin_from_xml):
                logging.error("To expect emulatorpin %s: %s",
                              cpu_list, emulatorpin_from_xml)
                return False

        return True


def get_emulatorpin_parameter(params, test):
    """
    Get the emulatorpin parameters
    :params: the parameter dictionary
    """
    vm_name = params.get("main_vm")
    vm = params.get("vm")
    options = params.get("emulatorpin_options")
    start_vm = params.get("start_vm", "yes")

    if start_vm == "no" and vm and vm.is_alive():
        vm.destroy()

    result = virsh.emulatorpin(vm_name, options=options, debug=True)
    status = result.exit_status

    # Check status_error
    status_error = params.get("status_error", "no")

    if status_error == "yes":
        if status or not check_emulatorpin(params, test):
            logging.info("It's an expected : %s", result.stderr)
        else:
            test.fail("%d not a expected command "
                      "return value" % status)
    elif status_error == "no":
        if status:
            test.fail(result.stderr)
        else:
            logging.info(result.stdout.strip())


def set_emulatorpin_parameter(params, test):
    """
    Set the emulatorpin parameters
    :params: the parameter dictionary
    """
    vm_name = params.get("main_vm")
    vm = params.get("vm")
    cpulist = params.get("emulatorpin_cpulist")
    options = params.get("emulatorpin_options")
    start_vm = params.get("start_vm", "yes")

    if start_vm == "no" and vm and vm.is_alive():
        vm.destroy()

    result = virsh.emulatorpin(vm_name, cpulist, options, debug=True)
    status = result.exit_status

    # Check status_error
    status_error = params.get("status_error")

    if status_error == "yes":
        if status or not check_emulatorpin(params, test):
            logging.info("It's an expected : %s", result.stderr)
        else:
            test.fail("%d not a expected command "
                      "return value" % status)
    elif status_error == "no":
        if status:
            test.fail(result.stderr)
        else:
            if check_emulatorpin(params, test):
                logging.info(result.stdout.strip())
            else:
                test.fail("The 'cpulist' is inconsistent with"
                          " 'cpulist' emulatorpin XML or/and is"
                          " different from emulator/cpuset.cpus"
                          " value from cgroup cpuset controller")


def run(test, params, env):
    """
    Test emulatorpin tuning

    1) Positive testing
       1.1) get the current emulatorpin parameters for a running/shutoff guest
       1.2) set the current emulatorpin parameters for a running/shutoff guest
    2) Negative testing
       2.1) get emulatorpin parameters for a running/shutoff guest
       2.2) set emulatorpin parameters running/shutoff guest
    """

    # Run test case
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    cgconfig = params.get("cgconfig", "on")
    cpulist = params.get("emulatorpin_cpulist")
    status_error = params.get("status_error", "no")
    change_parameters = params.get("change_parameters", "no")

    # Backup original vm
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    vmxml_backup = vmxml.copy()

    emulatorpin_placement = params.get("emulatorpin_placement", "")
    if emulatorpin_placement:
        vm.destroy()
        vmxml.placement = emulatorpin_placement
        vmxml.sync()
        try:
            vm.start()
        except VMStartError as detail:
            # Recover the VM and failout early
            vmxml_backup.sync()
            logging.debug("Used VM XML:\n %s", vmxml)
            test.fail("VM Fails to start: %s" % detail)

    test_dicts = dict(params)
    test_dicts['vm'] = vm

    host_cpus = cpu.online_count()
    test_dicts['host_cpus'] = host_cpus
    cpu_max = int(host_cpus) - 1

    cpu_list = None

    # Assemble cpu list for positive test
    if status_error == "no":
        if cpulist is None:
            pass
        elif cpulist == "x":
            cpu_online_map = list(map(str, cpu.online_list()))
            cpulist = random.choice(cpu_online_map)
        elif cpulist == "x-y":
            # By default, emulator is pined to all cpus, and element
            # 'cputune/emulatorpin' may not exist in VM's XML.
            # And libvirt will do nothing if pin emulator to the same
            # cpus, that means VM's XML still have that element.
            # So for testing, we should avoid that value(0-$cpu_max).
            if cpu_max < 2:
                cpulist = "0-0"
            else:
                cpulist = "0-%s" % (cpu_max - 1)
        elif cpulist == "x,y":
            cpu_online_map = list(map(str, cpu.online_list()))
            cpulist = ','.join(random.sample(cpu_online_map, 2))
        elif cpulist == "x-y,^z":
            cpulist = "0-%s,^%s" % (cpu_max, cpu_max)
        elif cpulist == "-1":
            cpulist = "-1"
        elif cpulist == "out_of_max":
            cpulist = str(cpu_max + 1)
        else:
            test.cancel("CPU-list=%s is not recognized."
                        % cpulist)
    test_dicts['emulatorpin_cpulist'] = cpulist
    if cpulist:
        cpu_list = cpus_parser(cpulist)
        test_dicts['cpu_list'] = cpu_list
        logging.debug("CPU list is %s", cpu_list)

    cg = utils_cgroup.CgconfigService()

    if cgconfig == "off":
        if cg.cgconfig_is_running():
            cg.cgconfig_stop()

    # positive and negative testing #########
    try:
        if status_error == "no":
            if change_parameters == "no":
                get_emulatorpin_parameter(test_dicts, test)
            else:
                set_emulatorpin_parameter(test_dicts, test)

        if status_error == "yes":
            if change_parameters == "no":
                get_emulatorpin_parameter(test_dicts, test)
            else:
                set_emulatorpin_parameter(test_dicts, test)
    finally:
        # Recover cgconfig and libvirtd service
        if not cg.cgconfig_is_running():
            cg.cgconfig_start()
            utils_libvirtd.libvirtd_restart()
        # Recover vm.
        vmxml_backup.sync()
