import re
import math
import os.path
import logging as log

from avocado.utils import cpu

from virttest import virsh
from virttest import libvirt_cgroup


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test virsh cpu-stats command.

    The command can display domain per-CPU and total statistics.
    1. Call virsh cpu-stats [domain]
    2. Call virsh cpu-stats [domain] with valid options
    3. Call virsh cpu-stats [domain] with invalid options
    """
    def get_cpuacct_info(suffix):
        """
        Get the CPU accounting info within the vm

        :param suffix: str, suffix of the CPU accounting.(stat/usage/usage_percpu)
        :return: list, the list of CPU accounting info
        """
        if 'cg_obj' not in locals():
            return
        # On cgroup v2 use cpu.stat as a substitute
        if cg_obj.is_cgroup_v2_enabled():
            cg_path = cg_obj.get_cgroup_path("cpu")
            para = ('cpu.%s' % suffix)
        else:
            cg_path = cg_obj.get_cgroup_path("cpuacct")
            para = ('cpuacct.%s' % suffix)
        # We only need the info in file which "emulator" is not in path
        if os.path.basename(cg_path) == "emulator":
            cg_path = os.path.dirname(cg_path)
        usage_file = os.path.join(cg_path, para)
        with open(usage_file, 'r') as f:
            cpuacct_info = f.read().strip().split()
        logging.debug("cpuacct info %s", cpuacct_info)
        return cpuacct_info

    def check_user_and_system_time(total_list):
        user_time = float(total_list[4])
        system_time = float(total_list[7])

        # Check libvirt user and system time between pre and next cgroup time
        # Unit conversion (Unit: second)
        # Default time unit is microseconds on cgroup v2 while 1/100 second on v1
        if cg_obj.is_cgroup_v2_enabled():
            pre_user_time = float(cpuacct_res_pre[3])/1000000
            pre_sys_time = float(cpuacct_res_pre[5])/1000000
            next_user_time = float(cpuacct_res_next[3])/1000000
            next_sys_time = float(cpuacct_res_next[5])/1000000
        else:
            pre_user_time = float(cpuacct_res_pre[1])/100
            pre_sys_time = float(cpuacct_res_pre[3])/100
            next_user_time = float(cpuacct_res_next[1])/100
            next_sys_time = float(cpuacct_res_next[3])/100

        # check user_time
        if next_user_time >= user_time >= pre_user_time:
            logging.debug("Got the expected user_time: %s", user_time)

        else:
            test.fail("Got unexpected user_time: %s, " % user_time +
                      "should between pre_user_time:%s " % pre_user_time +
                      "and next_user_time:%s" % next_user_time)

        # check system_time
        if next_sys_time >= system_time >= pre_sys_time:
            logging.debug("Got the expected system_time: %s", system_time)

        else:
            test.fail("Got unexpected system_time: %s, " % system_time +
                      "should between pre_sys_time:%s " % pre_sys_time +
                      "and next_sys_time:%s" % next_sys_time)

    if not virsh.has_help_command('cpu-stats'):
        test.cancel("This version of libvirt does not support "
                    "the cpu-stats test")

    vm_name = params.get("main_vm", "vm1")
    vm_ref = params.get("cpu_stats_vm_ref")
    status_error = params.get("status_error", "no")
    options = params.get("cpu_stats_options")
    error_msg = params.get("error_msg", "")
    logging.debug("options are %s", options)

    if vm_ref == "name":
        vm_ref = vm_name

    vm = env.get_vm(vm_ref)
    if vm and vm.get_pid():
        cg_obj = libvirt_cgroup.CgroupTest(vm.get_pid())
    # get host cpus num
    cpus = cpu.online_cpus_count()
    logging.debug("host online cpu num is %s", cpus)

    # get options and put into a dict
    get_total = re.search('total', options)
    get_start = re.search('start', options)
    get_count = re.search('count', options)

    # command without options
    get_noopt = 0
    if not get_total and not get_start and not get_count:
        get_noopt = 1

    # command with only --total option
    get_totalonly = 0
    if not get_start and not get_count and get_total:
        get_totalonly = 1

    option_dict = {}
    if options.strip():
        option_list = options.split('--')
        logging.debug("option_list is %s", option_list)
        for match in option_list[1:]:
            if get_start or get_count:
                option_dict[match.split(' ')[0]] = match.split(' ')[1]

    # check if cpu is enough,if not cancel the test
    if (status_error == "no"):
        cpu_start = int(option_dict.get("start", "0"))
        if cpu_start == 32:
            cpus = cpu.total_cpus_count()
            logging.debug("Host total cpu num: %s", cpus)
        if (cpu_start >= cpus):
            test.cancel("Host cpus are not enough")

    # get CPU accounting info twice to compare with user_time and system_time
    cpuacct_res_pre = get_cpuacct_info('stat')

    # Run virsh command
    cmd_result = virsh.cpu_stats(vm_ref, options,
                                 ignore_status=True, debug=True)
    output = cmd_result.stdout.strip()
    status = cmd_result.exit_status

    cpuacct_res_next = get_cpuacct_info('stat')

    # check status_error
    if status_error == "yes":
        if status == 0:
            test.fail("Run successfully with wrong command! Output: {}"
                      .format(output))
        else:
            # Check error message is expected
            if not re.search(error_msg, cmd_result.stderr.strip()):
                test.fail("Error message is not expected! "
                          "Expected: {} Actual: {}"
                          .format(error_msg, cmd_result.stderr.strip()))
    elif status_error == "no":
        if status != 0:
            test.fail("Run failed with right command! Error: {}"
                      .format(cmd_result.stderr.strip()))
        else:
            # Get cgroup cpu_time
            if not get_totalonly:
                cgtime = get_cpuacct_info('usage_percpu')

            # Cut CPUs from output and format to list
            if get_total:
                mt_start = re.search('Total', output).start()
            else:
                mt_start = len(output)
            output_cpus = " ".join(output[:mt_start].split())
            cpus_list = re.compile(r'CPU\d+:').split(output_cpus)

            # conditions that list total time info
            if get_noopt or get_total:
                mt_end = re.search('Total', output).end()
                total_list = output[mt_end + 1:].split()
                total_time = float(total_list[1])
                check_user_and_system_time(total_list)

            start_num = 0
            if get_start:
                start_num = int(option_dict["start"])

            end_num = int(cpus)
            if get_count:
                count_num = int(option_dict["count"])
                if end_num > start_num + count_num:
                    end_num = start_num + count_num

            # for only give --total option it only shows "Total" cpu info
            if get_totalonly:
                end_num = -1

            # find CPU[N] in output and sum the cpu_time and cgroup cpu_time
            sum_cputime = 0
            sum_cgtime = 0
            logging.debug("start_num %d, end_num %d", start_num, end_num)
            for i in range(start_num, end_num):
                logging.debug("Check CPU" + "%i" % i + " exist")
                sum_cputime += float(cpus_list[i - start_num + 1].split()[1])
                sum_cgtime += float(cgtime[i])
                if not re.search('CPU' + "%i" % i, output):
                    test.fail("Fail to find CPU" + "%i" % i + "in "
                              "result")

            # check cgroup cpu_time > sum of cpu_time
            if end_num >= 0:
                # usage_percpu reports the CPU time in nanoseconds
                sum_cgtime = sum_cgtime/1000000000
                logging.debug("Check sum of cgroup cpu_time %0.9f >= cpu_time %0.9f",
                              sum_cgtime, sum_cputime)
                if not (math.isclose(sum_cgtime, sum_cputime) or sum_cgtime > sum_cputime):
                    test.fail("Check sum of cgroup cpu_time < sum "
                              "of output cpu_time")

            # check Total cpu_time >= sum of cpu_time when no options
            if get_noopt:
                logging.debug("Check total time %0.9f >= sum of output cpu_time"
                              " %0.9f", total_time, sum_cputime)
                if not (math.isclose(total_time, sum_cputime) or total_time > sum_cputime):
                    test.fail("total time < sum of output cpu_time")
