import os
import re
import logging
import platform

from avocado.utils import process

from virttest import virsh
from virttest import utils_libvirtd
from virttest import utils_misc
from virttest import cpu
from virttest.libvirt_xml import capability_xml
from virttest.staging import utils_memory

from virttest import libvirt_version


def run(test, params, env):
    """
    Test the command virsh nodeinfo

    (1) Call virsh nodeinfo
    (2) Call virsh nodeinfo with an unexpected option
    (3) Call virsh nodeinfo with libvirtd service stop
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
                                  shell=True).stdout.strip()
        cmd = "cat /sys/devices/system/cpu/cpu*/online | wc -l"
        cpus_total = process.run(cmd, ignore_status=True,
                                 shell=True).stdout.strip()
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
        # Matching CPU Frequency is not an exact science in todays modern
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
        if not re.match(cores_per_socket_nodeinfo, cores_per_socket_os):
            # for spec NUMA arch, the output of nodeinfo is in a spec format
            cpus_os = cpu.get_cpu_info().get("CPU(s)")
            numa_cells_nodeinfo = _check_nodeinfo(
                nodeinfo_output, 'NUMA cell(s)', 3)
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

    # Prepare libvirtd service
    if "libvirtd" in params:
        libvirtd = params.get("libvirtd")
        if libvirtd == "off":
            utils_libvirtd.libvirtd_stop()

    # Run test case
    option = params.get("virsh_node_options")
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
