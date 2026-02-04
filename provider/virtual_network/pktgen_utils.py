import aexpect
import logging
import os

from avocado.utils import process

from virttest import data_dir
from virttest import remote
from virttest import utils_net
from virttest import utils_misc
from virttest import utils_package
from virttest.utils_test import libvirt

LOG_JOB = logging.getLogger("avocado.test")


class PktgenConfig:
    def __init__(self):
        self.interface = None
        self.dsc = None
        self.runner = None
        self.dest_path = "/tmp/"

    def configure_pktgen(
        self,
        pkt_cate,
        vm=None,
        session_serial=None,
        script=None,
        params=None
    ):
        """
        Configure pktgen test environment for different packet categories.

        :param pkt_cate: Packet category (tx, rx, loopback)
        :param vm: VM instance
        :param session_serial: Serial session for guest command execution
        :param script: Script name to execute
        :param params: Dictionary with the test parameters
        :return: Configured PktgenConfig instance
        """
        source_path = os.path.join(data_dir.get_shared_dir(), "scripts/pktgen_perf")
        guest_mac = vm.get_mac_address(0)
        guest_eth = utils_net.get_linux_ifname(session_serial, guest_mac)

        if pkt_cate == "tx":
            LOG_JOB.info("test guest tx pps performance")
            vm.copy_files_to(source_path, self.dest_path)
            if params.get("pktgen_tx_dst_mac"):
                self.dsc = params.get("pktgen_tx_dst_mac")
            else:
                host_iface = libvirt.get_ifname_host(vm.name, guest_mac)
                dsc_dev = utils_net.Interface(host_iface)
                self.dsc = dsc_dev.get_mac()
            self.interface = guest_eth
            self.runner = session_serial.cmd
        elif pkt_cate == "rx":
            LOG_JOB.info("test guest rx pps performance")
            if params.get("client"):
                client_ip = params.get("client")
                username = params.get("username_client", "root")
                password = params.get("password_client")
                remote.copy_files_to(
                    client_ip, "scp", username, password, 22,
                    source_path, self.dest_path, timeout=600
                )
                self.interface = params.get("pktgen_rx_iface")
                remote_session = remote.remote_login(
                    "ssh", client_ip, "22",
                    username, password, r'[$#%]'
                )
                self.runner = remote_session.cmd
            else:
                process.run("cp -r %s %s" % (source_path, self.dest_path))
                self.interface = libvirt.get_ifname_host(vm.name, guest_mac)
                self.runner = process.run
            self.dsc = guest_mac
        return self

    def generate_pktgen_cmd(
        self,
        script,
        pkt_cate,
        interface,
        dsc,
        threads,
        size,
        burst,
        session_serial=None,
    ):
        """
        Generate pktgen command based on test parameters.

        :param script: Script name to execute
        :param pkt_cate: Packet category (tx, rx, loopback)
        :param interface: Network interface
        :param dsc: Destination MAC address or IP
        :param threads: Number of threads
        :param size: Packet size
        :param burst: Burst size
        :param session_serial: Serial session for guest command execution
        :return: Generated command string
        """
        cmd = "%s -i %s -m %s -n 0 -t %s -s %s -b %s -c 0" % (
            "%spktgen_perf/%s.sh" % (self.dest_path, script),
            interface,
            dsc,
            threads,
            size,
            burst,
        )

        if (
            session_serial
            and self.runner == session_serial.cmd
        ):
            cmd = f"{cmd} &"

        return cmd


def run_test(script, cmd, runner, interface, timeout, session_serial=None, pkt_cate="tx"):
    """
    Run pktgen  script on remote and gather packet numbers/time and
    calculate mpps.
    :param script: pktgen script name.
    :param cmd: The command to execute the pktgen script
    :param runner: The command runner function
    :param interface: The VM Ethernet interface used to collect packet counters.
    :param timeout: The maximum time allowed for the test to run
    :param session_serial: Session serial for VM
    :param pkt_cate: Packet category (tx/rx), used to select counter type
    :return: The calculated MPPS (Million Packets Per Second)
    """
    counter = "rx_packets" if pkt_cate == "rx" else "tx_packets"
    packets = "cat /sys/class/net/%s/statistics/%s" % (interface, counter)
    LOG_JOB.info("Start pktgen test by cmd '%s'", cmd)
    try:
        packet_b = session_serial.cmd(packets)
        packet_a = None
        runner(cmd, timeout)
        packet_a = session_serial.cmd(packets)
    except aexpect.ShellTimeoutError:
        # when pktgen script is running on guest, the pktgen process
        # need to be killed.
        if session_serial and runner == session_serial.cmd:
            kill_cmd = (
                "kill -9 `ps -ef | grep %s --color | grep -v grep | "
                "awk '{print $2}'`" % script
            )
            runner(kill_cmd)
        packet_a = session_serial.cmd(packets)
    except process.CmdError:
        # when pktgen script is running on host, the pktgen process
        # will be quit when timeout triggers, so no need to kill it.
        packet_a = session_serial.cmd(packets)

    return "{:.2f}".format((int(packet_a) - int(packet_b)) / timeout / 10 ** 6)


def install_package(ver, pagesize=None, vm=None, session_serial=None):
    """
    Check module pktgen, install kernel-modules-internal package.

    :param ver: Kernel version string
    :param pagesize: Page size specification for kernel package selection
    :param vm: VM instance for guest installation
    :param session_serial: Serial session for guest command execution
    """
    result = process.run("which brew", ignore_status=True, shell=True)
    if result.exit_status != 0:
        utils_package.package_install("brewkoji")

    if pagesize:
        kernel_ver = "kernel-%s-modules-internal-%s" % (pagesize, ver.split("+")[0])
    else:
        kernel_ver = "kernel-modules-internal-%s" % ver
    cmd_download = "cd /tmp && brew download-build %s --rpm" % kernel_ver
    cmd_install = "cd /tmp && rpm -ivh  %s.rpm --force --nodeps" % kernel_ver

    utils_misc.cmd_status_output(cmd_download, shell=True)
    cmd_clean = "rm -rf /tmp/%s.rpm" % kernel_ver
    if session_serial:
        local_path = "/tmp/%s.rpm" % kernel_ver
        remote_path = "/tmp/"
        vm.copy_files_to(local_path, remote_path)
        utils_misc.cmd_status_output(cmd_install, session=session_serial)
        utils_misc.cmd_status_output(cmd_clean, session=session_serial)
    else:
        utils_misc.cmd_status_output(cmd_install, shell=True)
        utils_misc.cmd_status_output(cmd_clean, shell=True)


def format_result(result):
    """Format result with fixed width (12) and decimals (2)"""
    if isinstance(result, str):
        return "%12s" % result
    elif isinstance(result, int):
        return "%12d" % result
    elif isinstance(result, float):
        return "%12.2f" % result
    else:
        raise TypeError(f"unexpected result type: {type(result).__name__}")


def run_tests_for_category(
    params,
    result_file,
    test_vm=None,
    vm=None,
    session_serial=None,
):
    """
    Run Pktgen tests for a specific category.

    :param params: Dictionary with the test parameters
    :param result_file: File to write the test results
    :param test_vm: Flag indicating whether the test is running on a VM
    :param vm: VM instance
    :param session_serial: Session serial for VM
    """

    timeout = params.get_numeric("pktgen_test_timeout", "240")
    category = params.get("pkg_dir")
    record_list = params.get_list("record_list")
    pktgen_script = params.get("pktgen_script")
    # Get single values for threads and burst instead of looping
    threads = params.get("pktgen_threads", "")
    burst = params.get("burst", "")
    guest_mac = vm.get_mac_address(0)
    guest_eth = utils_net.get_linux_ifname(session_serial, guest_mac)

    record_line = ""
    for record in record_list:
        record_line += "%s|" % format_result(record)

    pktgen_config = PktgenConfig()

    for script in pktgen_script.split():
        for pkt_cate in category.split():
            result_file.write("Script:%s " % script)
            result_file.write("Category:%s\n" % pkt_cate)
            result_file.write("%s\n" % record_line.rstrip("|"))

            # Use single values directly since they're not lists
            size = params.get("pkt_size", "")
            pkt_cate_r = None

            if pkt_cate != "loopback":
                pktgen_config = pktgen_config.configure_pktgen(
                    pkt_cate, vm, session_serial, script=script, params=params
                )
                exec_cmd = pktgen_config.generate_pktgen_cmd(
                    script,
                    pkt_cate,
                    pktgen_config.interface,
                    pktgen_config.dsc,
                    threads,
                    size,
                    burst,
                    session_serial,
                )
                if exec_cmd:
                    pkt_cate_r = run_test(
                        script,
                        exec_cmd,
                        pktgen_config.runner,
                        guest_eth,
                        timeout,
                        session_serial,
                        pkt_cate=pkt_cate,
                    )

            line = "%s|" % format_result(size)
            line += "%s|" % format_result(threads)
            line += "%s|" % format_result(burst)
            line += "%s" % format_result(pkt_cate_r)
            result_file.write(("%s\n" % line))
