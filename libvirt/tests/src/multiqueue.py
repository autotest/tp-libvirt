import logging
import time
import threading
import re

from avocado.core import exceptions
from avocado.utils import process

from virttest import virsh
from virttest import remote
from virttest import libvirt_vm
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt as utlv


def config_xml_multiqueue(vm_name, vcpu=1, multiqueue=4):
    """
    Configure vCPU and interface for multiqueue test.
    """
    vm_xml.VMXML.set_vm_vcpus(vm_name, int(vcpu), int(vcpu))
    vm_xml.VMXML.set_multiqueues(vm_name, multiqueue)
    logging.debug("XML:%s", virsh.dumpxml(vm_name))


def get_channel_info(test, vm, interface="eth0"):
    """
    Get channel parameters of interface in vm.
    """
    cmd = "ethtool -l %s" % interface
    session = vm.wait_for_login()
    s, o = session.cmd_status_output(cmd)
    session.close()
    if s:
        test.fail("Get channel parameters for vm failed:%s"
                  % o)
    maximum = {}
    current = {}
    # Just for temp
    settings = {}
    for line in o.splitlines():
        if line.count("maximums"):
            settings = maximum
        elif line.count("Current"):
            settings = current
        parameter = line.split(':')[0].strip()
        if parameter in ["RX", "TX", "Other", "Combined"]:
            settings[parameter] = line.split(':')[1].strip()
    return maximum, current


def setting_channel(test, vm, interface, parameter, value):
    """
    Setting channel parameters for interface.
    """
    cmd = "ethtool -L %s %s %s" % (interface, parameter, value)
    session = vm.wait_for_login()
    s, o = session.cmd_status_output(cmd)
    session.close()
    if s:
        logging.debug("Setting %s to %s failed:%s", parameter, value, o)
        return False
    maximum, current = get_channel_info(test, vm, interface)
    try:
        if int(current['Combined']) == int(value):
            return True
    except KeyError:
        pass
    logging.debug("Setting passed, but checking failed:%s", current)
    return False


def get_vhost_pids(test, vm):
    vmpid = vm.get_pid()
    if vmpid is None:
        test.fail("Couldn't get vm's pid, its state: %s"
                  % vm.state())
    output = process.run("ps aux | grep [v]host-%s | awk '{print $2}'"
                         % vmpid, shell=True).stdout_text.strip()
    return output.splitlines()


def top_vhost(test, pids, expected_running_vhosts=1, timeout=15):
    """
    Use top tool to get vhost state.
    """
    pids_str = ','.join(pids)
    top_cmd = "top -n 1 -p %s -b" % pids_str
    timeout = int(timeout)
    while True:
        output = process.run(top_cmd, shell=True).stdout_text.strip()
        logging.debug(output)
        process_cpus = []
        for line in output.splitlines():
            if line.count("vhost"):
                process_cpus.append(line.split()[8])
        if len(process_cpus) != len(pids):
            test.fail("Couldn't get enough vhost processes.")
        running_vhosts = 0
        for cpu in process_cpus:
            if float(cpu):
                running_vhosts += 1
        if running_vhosts == int(expected_running_vhosts):
            break   # Got expected result
        else:
            if timeout > 0:
                timeout -= 3
                time.sleep(3)
                logging.debug("Trying again to avoid occassional...")
                continue
            else:
                test.fail("Couldn't get enough running vhosts:%s "
                          "from all vhosts:%s, other CPU status of "
                          "vhost is 0."
                          % (running_vhosts, len(pids)))


def set_cpu_affinity(vm, affinity_cpu=0):
    session = vm.wait_for_login()
    try:
        session.cmd("service irqbalance stop")
        output = session.cmd_output("cat /proc/interrupts")
        logging.debug(output)
        interrupts = []
        for line in output.splitlines():
            if re.search("virtio.*input", line):
                interrupts.append(line.split(':')[0].strip())
        # Setting
        for interrupt in interrupts:
            cmd = ("echo %s > /proc/irq/%s/smp_affinity"
                   % (affinity_cpu, interrupt))
            session.cmd(cmd)
    finally:
        session.close()


def get_cpu_affinity(vm):
    """
    Get cpu affinity.
    """
    session = vm.wait_for_login()
    output = session.cmd_output("cat /proc/interrupts")
    logging.debug(output)
    session.close()
    output = output.splitlines()
    cpu_count = len(output[0].split())
    cpu_state = {}
    for line in output[1:]:
        if re.search("virtio.*input", line):
            rows = line.split()
            interrupt_id = rows[0].strip().rstrip(':')
            state = []
            for count in range(cpu_count):
                state.append(rows[count+1])
            cpu_state[interrupt_id] = state
    return cpu_state


def check_cpu_affinity(test, vm, affinity_cpu_number):
    """
    Compare the distance of cpu usage to verify whether it's affinity.
    """
    cpu_state_before = get_cpu_affinity(vm)
    time.sleep(10)  # Set a distance
    cpu_state_after = get_cpu_affinity(vm)
    logging.debug("Before:%s", cpu_state_before)
    logging.debug("After:%s", cpu_state_after)
    if len(cpu_state_before) != len(cpu_state_after):
        test.fail("Get unmatched virtio input interrupts.")

    # Count cpu expanded size
    expand_cpu = {}
    for interrupt in list(cpu_state_before.keys()):
        before = cpu_state_before[interrupt]
        after = cpu_state_after[interrupt]
        expand_ipt = []
        for i in range(len(before)):
            expand_ipt.append(int(after[i]) - int(before[i]))
        expand_cpu[interrupt] = expand_ipt
    logging.debug("Distance: %s", expand_cpu)

    # Check affinity
    for interrupt in list(expand_cpu.keys()):
        expand_ipt = expand_cpu[interrupt]
        if max(expand_ipt) != expand_ipt[int(affinity_cpu_number)]:
            test.fail("Not affinity cpu to number %s: %s"
                      % (affinity_cpu_number, expand_ipt))


def get_vm_interface(vm, mac):
    session = vm.wait_for_login()
    links = session.cmd_output("ip link show")
    session.close()
    interface = None
    for line in links.splitlines():
        elems = line.split(':')
        if line.count("link/ether"):
            elems = line.split()
            if elems[1] == mac.lower():
                return interface
        else:
            elems = line.split(':')
            interface = elems[1].strip()
    return None


def prepare_vm_queue(test, vm, params):
    """
    Set vm queue for following test.
    """
    vcpu = params.get("vcpu_count", 1)
    queue = params.get("queue_count", 4)
    if vm.is_alive():
        vm.destroy()
    config_xml_multiqueue(vm.name, vcpu, queue)
    if vm.is_dead():
        vm.start()
    mac = vm.get_mac_address()
    interface = get_vm_interface(vm, mac)
    maximum, _ = get_channel_info(test, vm, interface)
    if int(maximum.get("Combined")) is not int(queue):
        test.fail("Set maximum queue is not effective:%s" % maximum)
    setting_channel(test, vm, interface, "combined", queue)
    _, current = get_channel_info(test, vm, interface)
    if int(current.get("Combined")) is not int(queue):
        test.fail("Set current queue is not effective:%s" % current)


def start_iperf(test, vm, params):
    """
    Start iperf server on host and run client in vm.
    """
    def iperf_func(session, server_ip, iperf_type, prefix=None, suffix=None):
        """
        Thread to run iperf.
        """
        if iperf_type == "server":
            cmd = "iperf -s"
        elif iperf_type == "client":
            cmd = "iperf -c %s -t 300" % server_ip
        if prefix:
            cmd = "%s %s" % (prefix, cmd)
        if suffix:
            cmd = "%s %s" % (cmd, suffix)
        session.cmd(cmd)

    server_ip = params.get("local_ip")
    server_pwd = params.get("local_pwd")
    queue_count = int(params.get("queue_count", 1))
    vcpu_count = int(params.get("vcpu_count", 1))
    if queue_count > vcpu_count:
        client_count = queue_count
    else:
        client_count = queue_count * 2
    host_session = remote.remote_login("ssh", server_ip, 22, "root",
                                       server_pwd, "#")
    # We may start several sessions for client
    client_sessions = []
    # Start server
    prefix = params.get("iperf_prefix")
    host_t = threading.Thread(target=iperf_func,
                              args=(host_session, server_ip, "server"))
    client_ts = []
    for count in range(client_count):
        client_session = vm.wait_for_login()
        client_t = threading.Thread(target=iperf_func,
                                    args=(client_session, server_ip,
                                          "client", prefix))
        client_sessions.append(client_session)
        client_ts.append(client_t)
    host_t.start()
    time.sleep(5)
    for client_t in client_ts:
        client_t.start()
    time.sleep(5)
    # Wait for iperf running
    try:
        if not host_t.isAlive():
            test.fail("Start iperf on server failed.")
        for client_t in client_ts:
            if not client_t.isAlive():
                test.fail("Start iperf on client failed.")
    except exceptions.TestFail:
        host_session.close()
        for client_session in client_sessions:
            client_session.close()
        host_t.join(2)
        for client_t in client_ts:
            client_t.join(2)
        raise
    # All iperf are working
    return host_session, client_sessions


def run(test, params, env):
    """
    Test multi function of vm devices.
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    # To avoid dirty after starting new vm
    if vm.is_alive():
        vm.destroy()
    new_vm_name = "mq_new_%s" % vm_name
    utlv.define_new_vm(vm_name, new_vm_name)
    # Create a new vm object for convenience
    new_vm = libvirt_vm.VM(new_vm_name, vm.params, vm.root_dir,
                           vm.address_cache)

    host_session = None
    client_sessions = []
    try:
        # Config new vm for multiqueue
        try:
            prepare_vm_queue(test, new_vm, params)
        except Exception:
            if int(params.get("queue_count")) > 8:
                params["queue_count"] = 8
                prepare_vm_queue(test, new_vm, params)

        # Start checking
        vhost_pids = get_vhost_pids(test, new_vm)
        logging.debug("vhosts: %s", vhost_pids)
        if len(vhost_pids) != int(params.get("queue_count")):
            test.fail("Vhost count is not matched with queue.")

        affinity_cpu_number = params.get("affinity_cpu_number", '1')
        # Here, cpu affinity should be in this format:
        # 0001 means CPU0, 0010 means CPU1...
        affinity_format = {'1': 2, '2': 4, '3': 8}
        if params.get("application") == "affinity":
            affinity_cpu = affinity_format[affinity_cpu_number]
            set_cpu_affinity(vm, affinity_cpu)

        # Run iperf
        # Use iperf to make interface busy, otherwise we may not get
        # expected results
        host_session, client_sessions = start_iperf(test, vm, params)
        # Wait for cpu consumed
        time.sleep(10)

        # Top to get vhost state or get cpu affinity
        if params.get("application") == "iperf":
            top_vhost(test, vhost_pids, params.get("vcpu_count", 1))
        elif params.get("application") == "affinity":
            check_cpu_affinity(test, vm, affinity_cpu_number)
    finally:
        if host_session:
            host_session.close()
        for client_session in client_sessions:
            client_session.close()
        if new_vm.is_alive():
            new_vm.destroy()
        new_vm.undefine()
