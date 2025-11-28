# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0
#   Author: Nannan Li<nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import aexpect
import time
import re

from avocado.utils import crypto
from avocado.utils import process

from virttest import remote, utils_misc, utils_net
from virttest import virsh
from virttest.libvirt_xml import vm_xml

from provider.virtual_network import network_base

VIRSH_ARGS = {'ignore_status': False, 'debug': True}


def run(test, params, env):
    """
    Test offload functions of ethernet device using ethtool in libvirt environment

    1) Log into a guest.
    2) Saving ethtool configuration.
    3) Enable sub function of NIC.
    4) Execute callback function.
    5) Disable sub function of NIC.
    6) Run callback function again.
    7) Run file transfer test.
       7.1) Creating file in source host.
       7.2) Listening network traffic with tcpdump command.
       7.3) Transfer file.
       7.4) Comparing md5sum of the files on guest and host.
    8) Repeat step 3 - 7.
    9) Restore original configuration.

    :param test: QEMU test object.
    :param params: Dictionary with the test parameters.
    :param env: Dictionary with test environment.
    """

    def ethtool_get(session):
        """
        Retrieve current ethernet device offload feature status using ethtool.

        This function queries the ethernet device's offload capabilities and returns
        the current status of various features like checksumming, segmentation offload,
        and receive offload features.

        :param session: VM session object for executing commands
        :type session: aexpect.ShellSession
        :return: Dictionary containing feature names as keys and their current status as values
                 Returns None for features that cannot be determined or are fixed
        :rtype: dict
        :raises IndexError: When ethtool output format doesn't match expected pattern
        """
        feature_pattern = {
            "tx": "tx.*checksumming",
            "rx": "rx.*checksumming",
            "sg": "scatter.*gather",
            "tso": "tcp.*segmentation.*offload",
            "gso": "generic.*segmentation.*offload",
            "gro": "generic.*receive.*offload",
            "lro": "large.*receive.*offload",
        }

        # TODO: Use JSON output for easier parsing and understanding:
        # eth_info = json.loads(session.cmd("ethtool --json -k %s" % ethname))
        o = session.cmd("ethtool -k %s" % ethname)
        test.log.debug("Get result:%s", o)
        status = {}
        for f in feature_pattern.keys():
            try:
                temp = re.findall("%s: (.*)" % feature_pattern.get(f), o)[0]
                if temp.find("[fixed]") != -1:
                    test.log.debug("%s is fixed", f)
                    continue
                status[f] = temp
            except IndexError:
                status[f] = None
                test.log.debug("(%s) failed to get status '%s'", ethname, f)

        test.log.debug("(%s) offload status: '%s'", ethname, str(status))
        return status

    def ethtool_set(session, status):
        """
        Set ethernet device offload status

        :param status: New status will be changed to
        """
        txt = "Set offload status for device "
        txt += "'%s': %s" % (ethname, str(status))
        test.log.debug(txt)

        cmd = "ethtool -K %s " % ethname
        cmd += " ".join([o + " " + s for o, s in status.items()])
        err_msg = "Failed to set offload status for device '%s'" % ethname
        try:
            o = session.cmd_output_safe(cmd)
            test.log.debug("The ethtool set cmd output:%s", o)
        except aexpect.ShellCmdError as e:
            test.log.error("%s, detail: %s", err_msg, e)
            return False

        curr_status = dict(
            (k, v) for k, v in ethtool_get(session).items() if k in status.keys()
        )
        if curr_status != status:
            test.log.error(
                "%s, got: '%s', expect: '%s'", err_msg, str(curr_status), str(status)
            )
            return False

        return True

    def ethtool_save_params(session):
        """
        Save current ethernet device offload configuration.

        This function captures and returns the current ethtool configuration
        of the ethernet device, which can be used later for restoration.

        :param session: VM session object for executing commands
        :type session: aexpect.ShellSession
        :return: Dictionary containing the current offload feature status
        :rtype: dict
        """
        test.log.debug("Saving ethtool configuration")
        return ethtool_get(session)

    def ethtool_restore_params(session, status):
        """
        Restore ethernet device offload configuration to previous state.

        This function compares the current ethtool configuration with the
        provided status and restores the device settings if they differ.

        :param session: VM session object for executing commands
        :type session: aexpect.ShellSession
        :param status: Previous offload configuration to restore
        :type status: dict
        """
        cur_stat = ethtool_get(session)
        if cur_stat != status:
            test.log.debug("Restoring ethtool configuration")
            ethtool_set(session, status)

    def compare_md5sum(name):
        """
        Compare MD5 checksums of files between guest and host.

        This function calculates and compares MD5 checksums of a file
        present on both the guest VM and the host to verify file integrity
        after transfer operations.

        # TODO: MD5 is slow and insecure. Consider using b3sum.
        :param name: File path to compare on both guest and host
        :type name: str
        :return: True if checksums match, False otherwise
        :rtype: bool
        :raises IndexError: When md5sum command output cannot be parsed
        """
        txt = "Comparing md5sum of the files on guest and host"
        test.log.debug(txt)
        host_result = crypto.hash_file(name, algorithm="md5")
        try:
            o = session.cmd_output("md5sum %s" % name)
            guest_result = re.findall(r"\w+", o)[0]
        except IndexError:
            test.log.error("Could not get file md5sum in guest")
            return False
        test.log.debug("md5sum: guest(%s), host(%s)", guest_result, host_result)
        return guest_result == host_result

    def transfer_file(src):
        """
        Transfer file by scp, use tcpdump to capture packets, then check the
        return string.

        :param src: Source host of transfer file
        :return: Tuple (status, error msg/tcpdump result)
        """
        #guest_ip = vm.get_address()
        vm.cleanup_serial_console()
        # Give it time to cleanup, remove it would cause login failed for this case
        time.sleep(2)
        vm.create_serial_console()
        session = vm.wait_for_serial_login(timeout=60)
        guest_ip = network_base.get_vm_ip(session, vm.get_mac_address(0))

        sess = remote.remote_login(
            "ssh", guest_ip, "22",
            params.get("username"), params.get("password"),
            r'[$#%]')
        session.cmd_output("rm -rf %s" % filename)
        dd_cmd = "dd if=/dev/urandom of=%s bs=1M count=%s" % (
            filename,
            params.get("filesize"),
        )
        failure = (False, "Failed to create file using dd, cmd: %s" % dd_cmd)
        txt = "Creating file in source host, cmd: %s" % dd_cmd
        test.log.debug(txt)
        ethname = utils_net.get_linux_ifname(session, vm.get_mac_address(0))
        tcpdump_cmd = "tcpdump -lep -i %s -s 0 tcp -vv port ssh" % ethname
        if src == "guest":
            tcpdump_cmd += " and src %s" % guest_ip
            copy_files_func = vm.copy_files_from
            try:
                session.cmd_output(dd_cmd, timeout=360)
            except aexpect.ShellCmdError:
                return failure
        else:
            tcpdump_cmd += " and dst %s" % guest_ip
            copy_files_func = vm.copy_files_to
            try:
                process.system(dd_cmd, shell=True)
            except process.CmdError:
                return failure

        # only capture the new tcp port after offload setup
        original_tcp_ports = re.findall(
            r"tcp.*:(\d+).*%s" % guest_ip,
            process.system_output("/bin/netstat -nap").decode(),
        )

        for i in original_tcp_ports:
            tcpdump_cmd += " and not port %s" % i

        txt = "Listening traffic using command: %s" % tcpdump_cmd
        test.log.debug(txt)
        sess.sendline(tcpdump_cmd)
        if not utils_misc.wait_for(
            lambda: session.cmd_status("pgrep tcpdump") == 0, 30
        ):
            return (False, "Tcpdump process wasn't launched")

        txt = "Transferring file %s from %s" % (filename, src)
        test.log.debug(txt)
        try:
            copy_files_func(filename, filename)
        except remote.SCPError as e:
            return (False, "File transfer failed (%s)" % e)

        session.cmd("killall tcpdump")
        try:
            tcpdump_string = sess.read_up_to_prompt(timeout=60)
        except aexpect.ExpectError:
            return (False, "Failed to read tcpdump's output")

        if not compare_md5sum(filename):
            return (False, "Failure, md5sum mismatch")
        return (True, tcpdump_string)

    def tx_callback():
        """
        Test callback function for transmit (TX) offload features.

        This function tests the transmit path by initiating a file transfer
        from the guest to the host and verifies the operation succeeds.

        :return: True if transfer succeeds, False otherwise
        :rtype: bool
        """
        s, o = transfer_file("guest")
        if not s:
            test.log.error(o)
            return False
        return True

    def rx_callback():
        """
        Test callback function for receive (RX) offload features.

        This function tests the receive path by initiating a file transfer
        from the host to the guest and verifies the operation succeeds.

        :return: True if transfer succeeds, False otherwise
        :rtype: bool
        """
        s, o = transfer_file("host")
        if not s:
            test.log.error(o)
            return False
        return True

    def so_callback(status="on"):
        """
        Test callback function for segmentation offload features (TSO/GSO).

        This function tests segmentation offload by transferring a file from guest
        to host and analyzing the network traffic to verify large frames are present
        when the feature is enabled. It performs XOR logic to validate behavior
        based on the offload status.

        :param status: Expected status of the offload feature ("on" or "off")
        :type status: str
        :return: True if segmentation behavior matches expected status, False otherwise
        :rtype: bool
        """
        s, o = transfer_file("guest")
        if not s:
            test.log.error(o)
            return False
        test.log.debug("Check if contained large frame")
        # MTU: default IPv4 MTU is 1500 Bytes, ethernet header is 14 Bytes
        return (status == "on") ^ (
            len([i for i in re.findall(r"length (\d*):", o) if int(i) > mtu]) == 0
        )

    def ro_callback():
        """
        Test callback function for receive offload features (GRO/LRO).

        This function tests receive offload features by initiating a file transfer
        from the host to the guest and verifies the operation succeeds.

        :return: True if transfer succeeds, False otherwise
        :rtype: bool
        """
        s, o = transfer_file("host")
        if not s:
            test.log.error(o)
            return False
        return True

    def setup_test():
        """
        Setup test environment and prepare VM with bridge/network configuration.
        """
        # Start VM if not already running
        if not vm.is_alive():
            test.log.debug("Starting VM: %s", vm_name)
            virsh.start(vm_name, **VIRSH_ARGS)
        test.log.debug("Test guest with xml:%s ", vm_xml.VMXML.new_from_dumpxml(vm_name))

    def run_test():
        """
        Execute the main ethtool offload feature testing procedure.

        This function performs the core testing logic by:
        1. Establishing VM session and saving initial ethtool configuration
        2. Iterating through supported features defined in test_matrix
        3. For each feature: enabling it, running callback tests, disabling it, and testing again
        4. Handling special cases for e1000/e1000e models where certain features are fixed
        5. Collecting and reporting any test failures
        6. Restoring original ethtool configuration in cleanup

        :raises TestFail: When any offload feature test fails
        """
        test.log.info("Check ethtool availability and run offload tests.")
        vm.cleanup_serial_console()
        # Give it time to cleanup, remove it would cause login failed for this case
        time.sleep(2)
        vm.create_serial_console()
        session = vm.wait_for_serial_login(timeout=60)

        pretest_status = ethtool_save_params(session)
        failed_tests = []

        try:
            for f_type in supported_features:
                callback = test_matrix[f_type][0]

                offload_stat = {f_type: "on"}
                offload_stat.update(dict.fromkeys(test_matrix[f_type][1], "on"))
                # lro is fixed for e1000 and e1000e, while trying to exclude
                # lro by setting "lro off", the command of ethtool returns error
                model_type = params.get("model", "")
                if not (
                    f_type == "gro"
                    and (
                        model_type == "e1000e"
                        or model_type == "e1000"
                    )
                ):
                    offload_stat.update(dict.fromkeys(test_matrix[f_type][2], "off"))
                if not ethtool_set(session, offload_stat):
                    e_msg = "Failed to set offload status"
                    test.log.error(e_msg)
                    failed_tests.append(e_msg)

                txt = "Run callback function %s" % callback.__name__
                test.log.debug(txt)

                # Some older kernel versions split packets by GSO
                # before tcpdump can capture the big packet, which
                # corrupts our results. Disable check when GSO is
                # enabled.
                if callback == so_callback:
                    callback_result = callback(status="on")
                else:
                    callback_result = callback()

                if not callback_result and f_type != "gso":
                    e_msg = "Callback failed after enabling %s" % f_type
                    test.log.error(e_msg)
                    failed_tests.append(e_msg)

                if not ethtool_set(session, {f_type: "off"}):
                    e_msg = "Failed to disable %s" % f_type
                    test.log.error(e_msg)
                    failed_tests.append(e_msg)
                txt = "Run callback function %s" % callback.__name__
                test.log.debug(txt)

                if callback == so_callback:
                    callback_result = callback(status="off")
                else:
                    callback_result = callback()

                if not callback_result:
                    e_msg = "Callback failed after disabling %s" % f_type
                    test.log.error(e_msg)
                    failed_tests.append(e_msg)

            if failed_tests:
                test.fail("Failed tests: %s" % failed_tests)

        finally:
            try:
                ethtool_restore_params(session, pretest_status)
            except Exception as detail:
                test.log.warning("Could not restore parameter of eth card: '%s'", detail)

    # Main test execution starts here
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    bridge_name = params.get("bridge_name")
    params.update({"netdst": bridge_name})
    login_timeout = params.get_numeric("login_timeout", 360)
    mtu = params.get_numeric("mtu", 1514)
    filename = "/tmp/ethtool.dd"

    supported_features = params.get_list("supported_features")
    if not supported_features:
        test.error("No supported features set on the parameters")

    test_matrix = {
        # 'type: (callback,    (dependence), (exclude)'
        "tx": (tx_callback, (), ()),
        "rx": (rx_callback, (), ()),
        "sg": (tx_callback, ("tx",), ()),
        "tso": (
            so_callback,
            (
                "tx",
                "sg",
            ),
            ("gso",),
        ),
        "gso": (so_callback, (), ("tso",)),
        "gro": (ro_callback, ("rx",), ("lro",)),
        "lro": (rx_callback, (), ("gro",)),
    }

    try:
        setup_test()
        # This case is transferred from qemu, the var session and ethname is used as global in
        # origin case, to avoid more update, let's leave it here.
        vm.create_serial_console()
        session = vm.wait_for_serial_login(timeout=login_timeout)
        ethname = utils_net.get_linux_ifname(session, vm.get_mac_address(0))

        run_test()
    finally:
        pass
