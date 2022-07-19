import logging as log

from avocado.utils import cpu as cpuutil

from virttest import virsh
from virttest import utils_libvirtd
from virttest import libvirt_version
from virttest import libvirt_cgroup

from virttest.utils_libvirt import libvirt_misc
from virttest.utils_test import libvirt

# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


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


def virsh_check_nodecpustats_percpu(test, actual_stats, cpu):
    """
    Check the actual nodecpustats output value
    total time <= total stat from proc

    :param test: test object
    :param actual_stats: Actual cpu stats
    :param cpu: cpu index

    :return: True if matches, else failout
    """

    # Normalise to seconds from nano seconds
    total = float((int(actual_stats['system']) + int(actual_stats['user']) +
                   int(actual_stats['idle']) + int(actual_stats['iowait'])) / (10 ** 9))

    expected = get_expected_stat(cpu)
    if not total <= expected['total']:
        test.fail("Commands 'virsh nodecpustats' not succeeded"
                  " as total time: %f is more"
                  " than proc/stat: %f" % (total, expected['total']))
    return True


def virsh_check_nodecpustats(test, actual_stats):
    """
    Check the actual nodecpustats output value
    total time <= total stat from proc

    :param test: test object
    :param actual_stats: Actual cpu stats
    :return: True if matches, else failout
    """

    # Normalise to seconds from nano seconds and get for one cpu
    total = float(((int(actual_stats['system']) + int(actual_stats['user']) +
                    int(actual_stats['idle']) + int(actual_stats['iowait'])) / (10 ** 9)))
    expected = get_expected_stat()
    if not total <= expected['total']:
        test.fail("Commands 'virsh nodecpustats' not succeeded"
                  " as total time: %f is more"
                  " than proc/stat: %f" % (total, expected['total']))
    return True


def virsh_check_nodecpustats_percentage(test, actual_per):
    """
    Check the actual nodecpustats percentage adds up to 100%

    :param test: test object
    :param actual_per: Actual cpu stats percentage
    :raises: test.fail if not match
    """
    total = int(round(float(actual_per['user']) + float(actual_per['system']) +
                      float(actual_per['idle']) + float(actual_per['iowait'])))

    if not total == 100:
        test.fail("Commands 'virsh nodecpustats' not succeeded"
                  " as the total percentage value: %d"
                  " is not equal 100" % total)


def run_nodecpustats(option=""):
    """
    Common utility function to run virsh nodecpustats

    :param option: virsh command option
    :return: a tuple (status, result)
    """
    output = virsh.nodecpustats(ignore_status=True, option=option, debug=True)
    status = output.exit_status
    return (status, output)


def subtest_no_any_option(test):
    """
    Run virsh nodecpustats and check result

    :param test: test object
    :raises: test.fail if command checking fails
    """
    option = ''
    status, output = run_nodecpustats(option)
    if not status:
        actual_value = libvirt_misc.convert_to_dict(output.stdout, r"^(\w+)\s*:\s+(\d+)")
        virsh_check_nodecpustats(test, actual_value)
    else:
        test.fail("Command 'virsh nodecpustats %s'"
                  " not succeeded" % option)


def subtest_cpu_option(test, cpu, index, with_cpu_option=True):
    """
    Run virsh nodecpustats --cpu xx and check result

    :param test: test object
    :param cpu: a specified host cpu
    :param index: cpu index in online host cpu list
    :param with_cpu_option: True, use '--cpu', otherwise, doesn't
    :raises: test.fail if command checking fails
    """
    option = "--cpu %s" % cpu if with_cpu_option else " %s" % cpu
    status, output = run_nodecpustats(option)
    if not status:
        actual_value = libvirt_misc.convert_to_dict(output.stdout, r"^(\w+)\s*:\s+(\d+)")
        virsh_check_nodecpustats_percpu(test, actual_value, index)
    else:
        test.fail("Command 'virsh nodecpustats %s'"
                  "not succeeded" % option)


def subtest_cpu_percentage_option(test, cpu, with_cpu_option=True):
    """
    Run virsh nodecpustats --cpu xxx --percent and check result

    :param test: test object
    :param cpu: a specified host cpu
    :param with_cpu_option: True, use '--cpu', otherwise, does not
    :raises: test.fail if command checking fails
    """
    option = "--cpu %s --percent" % cpu if with_cpu_option else " %s --percent" % cpu
    status, output = run_nodecpustats(option)
    if not status:
        actual_value = libvirt_misc.convert_to_dict(output.stdout, r"^(\w+)\s*:\s+(\d+.\d+)")
        virsh_check_nodecpustats_percentage(test, actual_value)
    else:
        test.fail("Command 'virsh nodecpustats %s'"
                  " not succeeded" % option)


def subtest_percentage_option(test):
    """
    Run virsh nodecpustats --percent and check result

    :param test: test object
    :raises: test.fail if command checking fails
    """
    # Test the total cpus to get the stats in percentage
    option = "--percent"
    status, output = run_nodecpustats(option)
    if not status:
        actual_value = libvirt_misc.convert_to_dict(output.stdout, r"^(\w+)\s*:\s+(\d+.\d+)")
        virsh_check_nodecpustats_percentage(test, actual_value)
    else:
        test.fail("Command 'virsh nodecpustats %s'"
                  " not succeeded" % option)


def test_all_options_all_cpus(test, host_cpus_list, params):
    """
    Test nodecpustats command with following setting:

    1. virsh nodecpustats
    2. virsh nodecpustats --percent
    3. virsh nodecpustats --cpu xx
    4. virsh nodecpustats xx
    5. virsh nodecpustats --cpu xx --percent
    6. virsh nodecpustats xx --percent

    :param test: test object
    :param host_cpus_list: list, host cpu list
    :param params: dict, test parameters
    """
    # Run test case for 1 iteration as default and can be changed
    # in subtests.cfg file
    itr = int(params.get("inner_test_iterations"))
    for i in range(itr):
        # Test with no any option
        subtest_no_any_option(test)
        # Test the total cpus to get the stats in percentage
        subtest_percentage_option(test)
        for idx, cpu in enumerate(host_cpus_list):
            # Test each cpu to get the cpu stats with --cpu
            subtest_cpu_option(test, cpu, idx)
            # Test each cpu to get the cpu stats without --cpu
            subtest_cpu_option(test, cpu, idx, with_cpu_option=False)
            # Test each cpu to get the cpu stats in percentage with --cpu
            subtest_cpu_percentage_option(test, cpu)
            # Test each cpu to get the cpu stats in percentage without --cpu
            subtest_cpu_percentage_option(test, cpu, with_cpu_option=False)


def test_disable_enable_cpu(test, host_cpus_list, params):
    """
    Test nodecpustats command when disable one cpu and then enable it respectively

    :param test: test object
    :param host_cpus_list: list, host cpu list
    :param params: dict, test parameters
    :raises: test.error if cpu offline or online fails
    """
    logging.debug("Offline host cpu %s" % host_cpus_list[-1])
    if cpuutil.offline(host_cpus_list[-1]):
        test.error("Failed to offline host cpu %s" % host_cpus_list[-1])
    option = "--cpu %s" % host_cpus_list[-1]
    status, output = run_nodecpustats(option)
    err_msg = params.get("err_msg", '')
    libvirt.check_result(output, expected_fails=[err_msg])

    logging.debug("Online host cpu %s" % host_cpus_list[-1])
    if cpuutil.online(host_cpus_list[-1]):
        test.error("Failed to online host cpu %s" % host_cpus_list[-1])
    subtest_cpu_percentage_option(test, host_cpus_list[-1], with_cpu_option=False)


def test_invalid_option(test, host_cpus_list, params):
    """
    Test nodecpustats command with invalid command option

    :param test:  test object
    :param host_cpus_list: list, host cpu list
    :param params: dict, test parameters
    :raises: test.fail if command checking fails
    """
    option = params.get("virsh_cpunodestats_options", '')
    status, _ = run_nodecpustats(option)
    if not status:
        test.fail("Command 'virsh nodecpustats %s' "
                  "succeeded with invalid option" % option)


def test_invalid_cpuNum(test, host_cpus_list, params):
    """
    Test nodecpustats command with different invalid cpu

    :param test: test object
    :param host_cpus_list: list, host cpu list
    :param params: dict, test parameters
    :return: None
    """

    for offset_value in [0, 5, 3200000000000000000000]:
        option = "--cpu %s" % (len(host_cpus_list) + offset_value)
        status, output = run_nodecpustats(option)
        err_msg = ''
        if (libvirt_version.version_compare(6, 2, 0)):
            err_msg = params.get('err_msg')
        libvirt.check_result(output, expected_fails=[err_msg])


def test_with_libvirtd_stop(test, host_cpus_list, params):
    """
    Test nodecpustats command when libvirt daemon is stopped

    :param test: test object
    :param host_cpus_list: list, host online cpu list
    :param params: dict, test parameters
    :raises: test.fail if command checking fails
    """
    utils_libvirtd.libvirtd_stop()
    status, _ = run_nodecpustats()
    if not status:
        if libvirt_version.version_compare(5, 6, 0):
            logging.debug("From libvirt version 5.6.0 libvirtd is restarted"
                          " and command should succeed")
        else:
            test.fail("Command 'virsh nodecpustats' "
                      "succeeded with libvirtd service "
                      "stopped, incorrect")


def run(test, params, env):
    """
    Test the command virsh nodecpustats

    Scenario 1: Test for all host cpus separately with all options,
                like --cpu, --percent
    Scenario 2: Disable one cpu, test nodecpustats command, then
                enable this cpu and test nodecpustats again
    Scenario 3: Test with invalid command option, like '--xyz'
    Scenario 4: Test with invalid cpu
    Scenario 5: Test with stopped libvirt daemons
    """
    test_case = params.get("test_case", "")
    run_test = eval("test_%s" % test_case)
    # Initialize the variables
    libvirtd = params.get("libvirtd", "on")

    try:
        # Get the host cpu list
        host_cpus_list = cpuutil.cpu_online_list()
        # CPU offline will change default cpuset and this change will not
        # be reverted after re-online that cpu on v1 cgroup.
        # Need to revert cpuset manually on v1 cgroup.
        if not libvirt_cgroup.CgroupTest(None).is_cgroup_v2_enabled():
            logging.debug("Need to keep original value in cpuset file under "
                          "cgroup v1 environment for later recovery")
            default_cpuset = libvirt_cgroup.CgroupTest(None).get_cpuset_cpus(params.get('main_vm'))
        run_test(test, host_cpus_list, params)
    finally:
        # recover v1 cgroup cpuset
        if not libvirt_cgroup.CgroupTest(None).is_cgroup_v2_enabled():
            logging.debug("Reset cpuset file under cgroup v1 environment")
            try:
                libvirt_cgroup.CgroupTest(None)\
                    .set_cpuset_cpus(default_cpuset, params.get('main_vm'))
            except Exception as e:
                test.error("Revert cpuset failed: {}".format(str(e)))
        # Recover libvirtd service state
        if libvirtd == "off":
            utils_libvirtd.libvirtd_start()
