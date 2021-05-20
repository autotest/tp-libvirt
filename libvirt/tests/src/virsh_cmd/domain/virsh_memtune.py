import logging
import re
import time

from virttest import libvirt_cgroup
from virttest import utils_libvirtd
from virttest import virsh
from virttest.staging import utils_memory
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


memtune_types = ['hard_limit', 'soft_limit', 'swap_hard_limit']


def check_limit(path, expected_value, limit_name, cgname, vm, test, acceptable_minus=8):
    """
    Matches the expected and actual output
    1) Match the output of the virsh memtune
    2) Match the output of the respective cgroup fs value
    3) Match the output of the virsh dumpxml
    4) Check if vm is alive

    :params path: memory controller path for a domain
    :params expected_value: the expected limit value
    :params limit_name: the limit type to be checked
                        hard-limit/soft-limit/swap-hard-limit
    :params cgname: the cgroup postfix
    :params vm: vm instance
    :params test: test instance
    """

    status_value = True

    logging.info("Check 1: Match the output of the virsh memtune")
    actual_value = virsh.memtune_get(vm.name, limit_name)
    minus = int(expected_value) - int(actual_value)
    if minus > acceptable_minus:
        status_value = False
        logging.error("%s virsh output:\n\tExpected value:%d"
                      "\n\tActual value: "
                      "%d", limit_name,
                      int(expected_value), int(actual_value))

    logging.info("Check 2: Match the output of the respective cgroup fs value")
    if int(expected_value) != -1:
        cg_file_name = '%s/%s' % (path, cgname)
        cg_file = None
        try:
            with open(cg_file_name) as cg_file:
                output = cg_file.read()
            logging.debug("cgroup file output is: %s", output)
            value = int(output) // 1024
            minus = int(expected_value) - int(value)
            if minus > acceptable_minus:
                status_value = False
                logging.error("%s cgroup fs:\n\tExpected Value: %d"
                              "\n\tActual Value: "
                              "%d", limit_name,
                              int(expected_value), int(value))
        except IOError as e:
            status_value = False
            logging.error("Error while reading:\n%s", cg_file_name)
            logging.error(e)

    logging.info("Check 3: Match the output of the virsh dumpxml")
    if int(expected_value) != -1:
        guest_xml = vm_xml.VMXML.new_from_dumpxml(vm.name)
        memtune_element = guest_xml.memtune
        logging.debug("Expected memtune XML is:\n%s", memtune_element)
        actual_fromxml = getattr(memtune_element, limit_name)
        if int(expected_value) != int(actual_fromxml):
            status_value = False
            logging.error("Expect memtune:\n%s\nBut got:\n "
                          "%s" % (expected_value, actual_fromxml))

    logging.info("Check 4: Check if vm is alive")
    if not vm.is_alive():
        status_value = False
        logging.error("Error: vm is not alive")

    if not status_value:
        test.fail("Failed to restore domain %s" % vm.name)


def check_limits(path, mt_limits, vm, test, acceptable_minus=8):
    """
    Check 3 types memtune setting in turn

    :params path: memory controller path for a domain
    :params mt_limits: a list with following items:
                       [str(hard_mem), str(soft_mem), str(swap_mem)]
    :params vm: vm instance
    :params test: test instance
    """
    for index in range(len(memtune_types)):
        check_limit(path, mt_limits[index], memtune_types[index],
                    mem_cgroup_info[memtune_types[index]], vm,
                    test, acceptable_minus)


def mem_step(params, path, vm, test, acceptable_minus=8):
    # Set the initial memory starting value for test case
    # By default set 1GB less than the total memory
    # In case of total memory is less than 1GB set to 256MB
    # visit subtests.cfg to change these default values
    base_mem = int(params.get("mt_base_mem"))
    hard_base = int(params.get("mt_hard_base_mem"))
    soft_base = int(params.get("mt_soft_base_mem"))

    # Get MemTotal of host
    Memtotal = utils_memory.read_from_meminfo('MemTotal')

    if int(Memtotal) < int(base_mem):
        Mem = int(params.get("mt_min_mem"))
    else:
        Mem = int(Memtotal) - int(base_mem)

    # Run test case with 100kB increase in memory value for each iteration
    start = time.time()
    while (Mem < Memtotal):
        # If time pass over 60 secondes, exit directly from while
        if time.time() - start > 60:
            break
        hard_mem = Mem - hard_base
        soft_mem = Mem - soft_base
        swaphard = Mem

        mt_limits = [str(hard_mem), str(soft_mem), str(swaphard)]
        options = " %s --live" % ' '.join(mt_limits)

        result = virsh.memtune_set(vm.name, options, debug=True)
        libvirt.check_exit_status(result)
        check_limits(path, mt_limits, vm, test, acceptable_minus)

        Mem += hard_base


def run(test, params, env):
    """
    Test the command virsh memtune

    1) To get the current memtune parameters
    2) Change the parameter values
    3) Check the memtune query updated with the values
    4) Check whether the mounted cgroup path gets the updated value
    5) Check the output of virsh dumpxml
    6) Check vm is alive
    """

    # Check for memtune command is available in the libvirt version under test
    if not virsh.has_help_command("memtune"):
        test.cancel(
            "Memtune not available in this libvirt version")

    # Check if memtune options are supported
    for option in memtune_types:
        option = re.sub('_', '-', option)
        if not virsh.has_command_help_match("memtune", option):
            test.cancel("%s option not available in memtune "
                        "cmd in this libvirt version" % option)
    # Get common parameters
    acceptable_minus = int(utils_memory.getpagesize() - 1)
    step_mem = params.get("mt_step_mem", "no") == "yes"
    expect_error = params.get("expect_error", "no") == "yes"
    restart_libvirtd = params.get("restart_libvirtd", "no") == "yes"
    set_one_line = params.get("set_in_one_command", "no") == "yes"
    mt_hard_limit = params.get("mt_hard_limit", None)
    mt_soft_limit = params.get("mt_soft_limit", None)
    mt_swap_hard_limit = params.get("mt_swap_hard_limit", None)
    # if restart_libvirtd is True, set set_one_line is True
    set_one_line = True if restart_libvirtd else set_one_line

    # Get the vm name, pid of vm and check for alive
    vm = env.get_vm(params["main_vm"])
    vm.verify_alive()
    pid = vm.get_pid()

    # Resolve the memory cgroup path for a domain
    cgtest = libvirt_cgroup.CgroupTest(pid)
    path = cgtest.get_cgroup_path("memory")
    logging.debug("cgroup path is %s", path)

    global mem_cgroup_info
    mem_cgroup_info = cgtest.get_cgroup_file_mapping(virsh_cmd='memtune')
    logging.debug("memtune cgroup info is %s", mem_cgroup_info)

    # step_mem is used to do step increment limit testing
    if step_mem:
        mem_step(params, path, vm, test, acceptable_minus)
        return

    if not set_one_line:
        # Set one type memtune limit in one command
        if mt_hard_limit:
            index = 0
            mt_limit = mt_hard_limit
        elif mt_soft_limit:
            index = 1
            mt_limit = mt_soft_limit
        elif mt_swap_hard_limit:
            index = 2
            mt_limit = mt_swap_hard_limit
        mt_type = memtune_types[index]
        mt_cgname = mem_cgroup_info[mt_type]
        options = " --%s %s --live" % (re.sub('_', '-', mt_type), mt_limit)
        result = virsh.memtune_set(vm.name, options, debug=True)

        if expect_error:
            fail_patts = [params.get("error_info")]
            libvirt.check_result(result, fail_patts, [])
        else:
            # If limit value is negative, means no memtune limit
            mt_expected = mt_limit if int(mt_limit) > 0 else -1
            check_limit(path, mt_expected, mt_type, mt_cgname, vm, test,
                        acceptable_minus)
    else:
        # Set 3 limits in one command line
        mt_limits = [mt_hard_limit, mt_soft_limit, mt_swap_hard_limit]
        options = " %s --live" % ' '.join(mt_limits)
        result = virsh.memtune_set(vm.name, options, debug=True)

        if expect_error:
            fail_patts = [params.get("error_info")]
            libvirt.check_result(result, fail_patts, [])
        else:
            check_limits(path, mt_limits, vm, test, acceptable_minus)

        if restart_libvirtd:
            libvirtd = utils_libvirtd.Libvirtd()
            libvirtd.restart()

        if not expect_error:
            # After libvirtd restared, check memtune values again
            check_limits(path, mt_limits, vm, test, acceptable_minus)
