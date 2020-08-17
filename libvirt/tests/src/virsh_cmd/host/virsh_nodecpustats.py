import re
import logging

from avocado.utils import cpu as cpuutil

from virttest import virsh
from virttest import utils_libvirtd
from virttest import libvirt_version
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Test the command virsh nodecpustats

    (1) Call the virsh nodecpustats command for all cpu host cpus
        separately
    (2) Get the output
    (3) Check the against /proc/stat output(o) for respective cpu
        user: o[0] + o[1]
        system: o[2] + o[5] + o[6]
        idle: o[3]
        iowait: o[4]
    (4) Call the virsh nodecpustats command with an unexpected option
    (5) Call the virsh nodecpustats command with libvirtd service stop
    """

    def get_expected_stat(cpu=None):
        """
        Parse cpu stats from /proc/stat

        :param cpu: cpu index, None for total cpu stat
        :return: dict of cpu stats
        """
        stats = {}
        cpu_stat = []
        with open("/proc/stat", "r") as fl:
            for line in fl.readlines():
                if line.startswith("cpu"):
                    cpu_stat.append(line.strip().split(" ")[1:])
        # Delete additional space in the total cpu stats line
        del cpu_stat[0][0]
        if cpu is None:
            idx = 0
        else:
            idx = int(cpu) + 1
        stats['user'] = int(cpu_stat[idx][0]) + int(cpu_stat[idx][1])
        stats['system'] = int(cpu_stat[idx][2]) + int(cpu_stat[idx][5]) + int(cpu_stat[idx][6])
        stats['idle'] = int(cpu_stat[idx][3])
        stats['iowait'] = int(cpu_stat[idx][4])
        stats['total'] = stats['user'] + stats['system'] + stats['idle'] + stats['iowait']
        return stats

    def virsh_check_nodecpustats_percpu(actual_stats, cpu):
        """
        Check the actual nodecpustats output value
        total time <= total stat from proc

        :param actual_stats: Actual cpu stats
        :param cpu: cpu index

        :return: True if matches, else failout
        """

        # Normalise to seconds from nano seconds
        total = float((actual_stats['system'] + actual_stats['user'] +
                       actual_stats['idle'] + actual_stats['iowait']) / (10 ** 9))

        expected = get_expected_stat(cpu)
        if not total <= expected['total']:
            test.fail("Commands 'virsh nodecpustats' not succeeded"
                      " as total time: %f is more"
                      " than proc/stat: %f" % (total, expected['total']))
        return True

    def virsh_check_nodecpustats(actual_stats):
        """
        Check the actual nodecpustats output value
        total time <= total stat from proc

        :param actual_stats: Actual cpu stats
        :return: True if matches, else failout
        """

        # Normalise to seconds from nano seconds and get for one cpu
        total = float(((actual_stats['system'] + actual_stats['user'] +
                        actual_stats['idle'] + actual_stats['iowait']) / (10 ** 9)))
        expected = get_expected_stat()
        if not total <= expected['total']:
            test.fail("Commands 'virsh nodecpustats' not succeeded"
                      " as total time: %f is more"
                      " than proc/stat: %f" % (total, expected['total']))
        return True

    def virsh_check_nodecpustats_percentage(actual_per):
        """
        Check the actual nodecpustats percentage adds up to 100%

        :param actual_per: Actual cpu stats percentage
        :return: True if matches, else failout
        """

        total = int(round(actual_per['user'] + actual_per['system'] +
                          actual_per['idle'] + actual_per['iowait']))

        if not total == 100:
            test.fail("Commands 'virsh nodecpustats' not succeeded"
                      " as the total percentage value: %d"
                      " is not equal 100" % total)

    def parse_output(output):
        """
        To get the output parsed into a dictionary

        :param output: virsh command output
        :return: dict of user,system,idle,iowait times
        """

        # From the beginning of a line, group 1 is one or more word-characters,
        # followed by zero or more whitespace characters and a ':',
        # then one or more whitespace characters,
        # followed by group 2, which is one or more digit characters,
        # e.g as below
        # user:                  6163690000000
        #
        regex_obj = re.compile(r"^(\w+)\s*:\s+(\d+)")
        actual = {}

        for line in output.stdout.split('\n'):
            match_obj = regex_obj.search(line)
            # Due to the extra space in the list
            if match_obj is not None:
                name = match_obj.group(1)
                value = match_obj.group(2)
                actual[name] = int(value)
        return actual

    def parse_percentage_output(output):
        """
        To get the output parsed into a dictionary

        :param output: virsh command output
        :return: dict of user,system,idle,iowait times
        """

        # From the beginning of a line, group 1 is one or more word-characters,
        # followed by zero or more whitespace characters and a ':',
        # then one or more whitespace characters,
        # followed by group 2, which is one or more digit characters,
        # e.g as below
        # user:             1.5%
        #
        regex_obj = re.compile(r"^(\w+)\s*:\s+(\d+.\d+)")
        actual_percentage = {}

        for line in output.stdout.split('\n'):
            match_obj = regex_obj.search(line)
            # Due to the extra space in the list
            if match_obj is not None:
                name = match_obj.group(1)
                value = match_obj.group(2)
                actual_percentage[name] = float(value)
        return actual_percentage

    # Initialize the variables
    itr = int(params.get("inner_test_iterations"))
    option = params.get("virsh_cpunodestats_options")
    invalid_cpunum = params.get("invalid_cpunum")
    status_error = params.get("status_error")
    libvirtd = params.get("libvirtd", "on")

    # Prepare libvirtd service
    if libvirtd == "off":
        utils_libvirtd.libvirtd_stop()

    # Get the host cpu list
    host_cpus_list = cpuutil.cpu_online_list()

    # Run test case for 5 iterations default can be changed in subtests.cfg
    # file
    for i in range(itr):

        if status_error == "yes":
            if invalid_cpunum == "yes":
                option = "--cpu %s" % (len(host_cpus_list) + 1)
            output = virsh.nodecpustats(ignore_status=True, option=option)
            status = output.exit_status

            if status == 0:
                if libvirtd == "off":
                    if libvirt_version.version_compare(5, 6, 0):
                        logging.debug("From libvirt version 5.6.0 libvirtd is restarted"
                                      " and command should succeed")
                    else:
                        utils_libvirtd.libvirtd_start()
                        test.fail("Command 'virsh nodecpustats' "
                                  "succeeded with libvirtd service "
                                  "stopped, incorrect")
                else:
                    test.fail("Command 'virsh nodecpustats %s' "
                              "succeeded (incorrect command)" % option)
            if (invalid_cpunum == "yes" and
               libvirt_version.version_compare(6, 2, 0)):
                err_msg = "Invalid cpuNum in virHostCPUGetStatsLinux"
                libvirt.check_result(output, expected_fails=[err_msg])

        elif status_error == "no":
            # Run the testcase for each cpu to get the cpu stats
            for idx, cpu in enumerate(host_cpus_list):
                option = "--cpu %s" % cpu
                output = virsh.nodecpustats(ignore_status=True, option=option)
                status = output.exit_status

                if status == 0:
                    actual_value = parse_output(output)
                    virsh_check_nodecpustats_percpu(actual_value, idx)
                else:
                    test.fail("Command 'virsh nodecpustats %s'"
                              "not succeeded" % option)

            # Run the test case for each cpu to get the cpu stats in percentage
            for cpu in host_cpus_list:
                option = "--cpu %s --percent" % cpu
                output = virsh.nodecpustats(ignore_status=True, option=option)
                status = output.exit_status

                if status == 0:
                    actual_value = parse_percentage_output(output)
                    virsh_check_nodecpustats_percentage(actual_value)
                else:
                    test.fail("Command 'virsh nodecpustats %s'"
                              " not succeeded" % option)

            option = ''
            # Run the test case for total cpus to get the cpus stats
            output = virsh.nodecpustats(ignore_status=True, option=option)
            status = output.exit_status

            if status == 0:
                actual_value = parse_output(output)
                virsh_check_nodecpustats(actual_value)
            else:
                test.fail("Command 'virsh nodecpustats %s'"
                          " not succeeded" % option)

            # Run the test case for the total cpus to get the stats in
            # percentage
            option = "--percent"
            output = virsh.nodecpustats(ignore_status=True, option=option)
            status = output.exit_status

            if status == 0:
                actual_value = parse_percentage_output(output)
                virsh_check_nodecpustats_percentage(actual_value)
            else:
                test.fail("Command 'virsh nodecpustats %s'"
                          " not succeeded" % option)

    # Recover libvirtd service start
    if libvirtd == "off":
        utils_libvirtd.libvirtd_start()
