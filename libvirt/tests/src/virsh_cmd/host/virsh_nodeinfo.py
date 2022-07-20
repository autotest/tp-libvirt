import os
import re
import logging as log
import platform

from avocado.utils import cpu as cputils
from avocado.utils import process

from virttest import cpu
from virttest import libvirt_cgroup
from virttest import libvirt_version
from virttest import utils_libvirtd
from virttest import utils_misc
from virttest import virsh

from virttest.libvirt_xml import capability_xml
from virttest.staging import utils_memory


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test the command virsh nodeinfo

    (1) Call virsh nodeinfo
    (2) Call virsh nodeinfo with an unexpected option
    (3) Call virsh nodeinfo with libvirtd service stop
    (4) Disable/enable vcpu, then call virsh nodeinfo
    """
    def _check_nodeinfo(nodeinfo_output, verify_str, column):
        cmd = "echo \"%s\" | grep \"%s\" | awk '{print $%s}'" % (
            nodeinfo_output, verify_str, column)
        cmd_result = process.run(cmd, ignore_status=True, shell=True)
        stdout = cmd_result.stdout_text.strip()
        logging.debug("Info %s on nodeinfo output:%s" % (verify_str, stdout))
        return stdout

    def output_check(nodeinfo_output):
        # Check CPU model
        cpu_model_nodeinfo = _check_nodeinfo(nodeinfo_output, "CPU model", 3)
        cpu_arch = platform.machine()
        if not re.match(cpu_model_nodeinfo, cpu_arch):
            test.fail(
                "Virsh nodeinfo output didn't match CPU model")

        # Check number of CPUs, nodeinfo CPUs represent online threads in the
        # system, check all online cpus in sysfs
        cpus_nodeinfo = _check_nodeinfo(nodeinfo_output, "CPU(s)", 2)
        cmd = "cat /sys/devices/system/cpu/cpu*/online | grep 1 | wc -l"
        cpus_online = process.run(cmd, ignore_status=True,
                                  shell=True).stdout_text.strip()

        cmd = "cat /sys/devices/system/cpu/cpu*/online | wc -l"
        cpus_total = process.run(cmd, ignore_status=True,
                                 shell=True).stdout_text.strip()

        if not os.path.exists('/sys/devices/system/cpu/cpu0/online'):
            cpus_online = str(int(cpus_online) + 1)
            cpus_total = str(int(cpus_total) + 1)

        logging.debug("host online cpus are %s", cpus_online)
        logging.debug("host total cpus are %s", cpus_total)

        if cpus_nodeinfo != cpus_online:
            if 'ppc' in cpu_arch:
                if cpus_nodeinfo != cpus_total:
                    test.fail("Virsh nodeinfo output of CPU(s) on"
                              " ppc did not match all threads in "
                              "the system")
            else:
                test.fail("Virsh nodeinfo output didn't match "
                          "number of CPU(s)")

        # Check CPU frequency, frequency is under clock for ppc
        cpu_frequency_nodeinfo = _check_nodeinfo(
            nodeinfo_output, 'CPU frequency', 3)
        cmd = ("cat /proc/cpuinfo | grep -E 'cpu MHz|clock|BogoMIPS' | "
               "head -n1 | awk -F: '{print $2}' | awk -F. '{print $1}'")
        cmd_result = process.run(cmd, ignore_status=True, shell=True)
        cpu_frequency_os = cmd_result.stdout_text.strip()
        logging.debug("cpu_frequency_nodeinfo=%s cpu_frequency_os=%s",
                      cpu_frequency_nodeinfo, cpu_frequency_os)
        #
        # Matching CPU Frequency is not an exact science in today's modern
        # processors and OS's. CPU's can have their execution speed varied
        # based on current workload in order to save energy and keep cool.
        # Thus since we're getting the values at disparate points in time,
        # we cannot necessarily do a pure comparison.
        # So, let's get the absolute value of the difference and ensure
        # that it's within 20 percent of each value to give us enough of
        # a "fudge" factor to declare "close enough". Don't return a failure
        # just print a debug message and move on.
        diffval = abs(int(cpu_frequency_nodeinfo) - int(cpu_frequency_os))
        if (float(diffval) / float(cpu_frequency_nodeinfo) > 0.20 or
                float(diffval) / float(cpu_frequency_os) > 0.20):
            logging.debug("Virsh nodeinfo output didn't match CPU "
                          "frequency within 20 percent")

        # Get CPU topology from virsh capabilities xml
        cpu_topology = capability_xml.CapabilityXML()['cpu_topology']
        logging.debug("Cpu topology in virsh capabilities output: %s",
                      cpu_topology)

        # Check CPU socket(s)
        cpu_sockets_nodeinfo = int(
            _check_nodeinfo(nodeinfo_output, 'CPU socket(s)', 3))
        # CPU socket(s) in virsh nodeinfo is Total sockets in each node, not
        # total sockets in the system, so get total sockets in one node and
        # check with it
        node_info = utils_misc.NumaInfo()
        node_online_list = node_info.get_online_nodes()
        cmd = "cat /sys/devices/system/node/node%s" % node_online_list[0]
        cmd += "/cpu*/topology/physical_package_id | uniq |wc -l"
        cmd_result = process.run(cmd, ignore_status=True, shell=True)
        total_sockets_in_node = int(cmd_result.stdout_text.strip())
        if total_sockets_in_node != cpu_sockets_nodeinfo:
            test.fail("Virsh nodeinfo output didn't match CPU "
                      "socket(s) of host OS")
        if cpu_sockets_nodeinfo != int(cpu_topology['sockets']):
            test.fail("Virsh nodeinfo output didn't match CPU "
                      "socket(s) of virsh capabilities output")

        # Check Core(s) per socket
        cores_per_socket_nodeinfo = _check_nodeinfo(
            nodeinfo_output, 'Core(s) per socket', 4)
        cmd = "lscpu | grep 'Core(s) per socket' | head -n1 | awk '{print $4}'"
        cmd_result = process.run(cmd, ignore_status=True, shell=True)
        cores_per_socket_os = cmd_result.stdout_text.strip()
        spec_numa = False

        numa_cells_nodeinfo = _check_nodeinfo(
            nodeinfo_output, 'NUMA cell(s)', 3)
        logging.debug("cpu_sockets_nodeinfo=%s", cpu_sockets_nodeinfo)
        logging.debug("numa_cells_nodeinfo=%s", numa_cells_nodeinfo)

        lscpu_info_dict = cpu.get_cpu_info()
        numa_cells_os = lscpu_info_dict.get('NUMA node(s)')
        if numa_cells_nodeinfo != numa_cells_os:
            test.fail("Virsh nodeinfo output '%s' didn't match "
                      "NUMA node(s) of host OS '%s'" % (numa_cells_nodeinfo,
                                                        numa_cells_os))
        cpus_os = lscpu_info_dict.get("CPU(s)")
        if not re.match(cores_per_socket_nodeinfo, cores_per_socket_os):
            # for spec NUMA arch, the output of nodeinfo is in a spec format
            if (re.match(cores_per_socket_nodeinfo, cpus_os) and
                    re.match(numa_cells_nodeinfo, "1")):
                spec_numa = True
            else:
                test.fail("Virsh nodeinfo output didn't match "
                          "CPU(s) or Core(s) per socket of host OS")

        if cores_per_socket_nodeinfo != cpu_topology['cores']:
            test.fail("Virsh nodeinfo output didn't match Core(s) "
                      "per socket of virsh capabilities output")
        # Check Thread(s) per core
        threads_per_core_nodeinfo = _check_nodeinfo(nodeinfo_output,
                                                    'Thread(s) per core', 4)

        multiplied_node_value = int(cores_per_socket_nodeinfo) * int(numa_cells_nodeinfo) * int(cpu_sockets_nodeinfo) * int(threads_per_core_nodeinfo)
        logging.debug("The multiplied node value for CPU socket(s)*"
                      "Core(s) per socket*Thread(s) per core*"
                      "NUMA cell(s) in nodeinfo is '%d'", multiplied_node_value)
        if int(cpus_os) != multiplied_node_value:
            test.fail("CPU socket(s) * Core(s) per socket * "
                      "Thread(s) per core * NUMA cell(s) in nodeinfo "
                      "is expected to be equal to CPU(s) in lscpu '%s', "
                      "but found '%d'" % (cpus_os, multiplied_node_value))

        if not spec_numa:
            if threads_per_core_nodeinfo != cpu_topology['threads']:
                test.fail("Virsh nodeinfo output didn't match"
                          "Thread(s) per core of virsh"
                          "capabilities output")
        else:
            if threads_per_core_nodeinfo != "1":
                test.fail("Virsh nodeinfo output didn't match"
                          "Thread(s) per core of virsh"
                          "capabilities output")
        # Check Memory size
        memory_size_nodeinfo = int(
            _check_nodeinfo(nodeinfo_output, 'Memory size', 3))
        memory_size_os = 0
        if libvirt_version.version_compare(2, 0, 0):
            for i in node_online_list:
                node_memory = node_info.read_from_node_meminfo(i, 'MemTotal')
                memory_size_os += int(node_memory)
        else:
            memory_size_os = utils_memory.memtotal()
        logging.debug('The host total memory from nodes is %s', memory_size_os)

        if memory_size_nodeinfo != memory_size_os:
            test.fail("Virsh nodeinfo output didn't match "
                      "Memory size")

    def test_disable_enable_cpu():
        """
        Test disable a host cpu and check nodeinfo result

        :return: test.fail if CPU(s) number is not expected
        """
        def _get_nodeinfo():
            the_nodeinfo = virsh.nodeinfo(ignore_status=True, debug=True)
            return _check_nodeinfo(the_nodeinfo.stdout_text.strip(), "CPU(s)", 2)

        is_cgroupv2 = libvirt_cgroup.CgroupTest(None).is_cgroup_v2_enabled()
        if not is_cgroupv2:
            logging.debug("Need to keep original value in cpuset file under "
                          "cgroup v1 environment for later recovery")
            default_cpuset = libvirt_cgroup.CgroupTest(None).get_cpuset_cpus(params.get("main_vm"))

        cpus_nodeinfo_before = _get_nodeinfo()
        logging.debug("Now a host cpu is turned to be offline")
        online_list = cputils.online_list()
        # Choose the last online host cpu to offline
        cputils.offline(online_list[-1])

        cpus_nodeinfo_after = _get_nodeinfo()
        if int(cpus_nodeinfo_before) != int(cpus_nodeinfo_after) + 1:
            test.fail("CPU(s) should be '%d' after 1 cpu is offline, "
                      "but found '%s'" % (int(cpus_nodeinfo_before) - 1,
                                          cpus_nodeinfo_after))
        logging.debug("Now a host cpu is turned to be online")
        # Make the last host cpu online again
        cputils.online(online_list[-1])
        if not is_cgroupv2:
            logging.debug("Reset cpuset file under cgroup v1 environment")
            libvirt_cgroup.CgroupTest(None).set_cpuset_cpus(default_cpuset)

        cpus_nodeinfo_after = _get_nodeinfo()
        if int(cpus_nodeinfo_before) != int(cpus_nodeinfo_after):
            test.fail("CPU(s) should be '%d' after 1 cpu is online, "
                      "but found '%s'" % (int(cpus_nodeinfo_before),
                                          cpus_nodeinfo_after))

    # Prepare libvirtd service

    libvirtd = params.get("libvirtd", "on")
    if libvirtd == 'off':
        utils_libvirtd.libvirtd_stop()

    # Run test case
    option = params.get("virsh_node_options")
    disable_enable_vcpu = "yes" == params.get("disable_enable_vcpu", "no")
    # Test vcpu disable if needed
    if disable_enable_vcpu:
        test_disable_enable_cpu()
        return

    cmd_result = virsh.nodeinfo(ignore_status=True, extra=option)
    logging.info("Output:\n%s", cmd_result.stdout.strip())
    logging.info("Status: %d", cmd_result.exit_status)
    logging.error("Error: %s", cmd_result.stderr.strip())
    output = cmd_result.stdout.strip()
    status = cmd_result.exit_status

    # Recover libvirtd service start
    if libvirtd == "off":
        utils_libvirtd.libvirtd_start()

    # Check status_error
    status_error = params.get("status_error")

    if status_error == "yes":
        if status == 0:
            if libvirtd == "off" and libvirt_version.version_compare(5, 6, 0):
                logging.info("From libvirt version 5.6.0 libvirtd is restarted "
                             "and command should succeed")
            else:
                test.fail("Command 'virsh nodeinfo %s' succeeded"
                          "(incorrect command)" % option)
    elif status_error == "no":
        output_check(output)
        if status != 0:
            test.fail("Command 'virsh nodeinfo %s' failed "
                      "(correct command)" % option)
