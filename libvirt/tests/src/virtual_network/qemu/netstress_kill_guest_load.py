# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0
#
#   Author: Adapted from tp-qemu netstress_kill_guest test
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

import os
import time

from avocado.utils import process

from virttest import (
    data_dir,
    virsh,
    utils_misc,
    utils_net,
    utils_netperf,
)
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml

# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.

VIRSH_ARGS = {'debug': True, 'ignore_status': False}


def run(test, params, env):
    """
    Try to kill the guest during network stress in guest using libvirt commands.

    This test specifically focuses on the 'load' mode variant:
    1) Boot up VM and establish connection
    2) Stop iptables in guest and host
    3) Setup netperf server in host and guest
    4) Start heavy network load host <=> guest by running netperf client
    5) During netperf running, check that we can destroy VM with virsh
    6) Clean up netperf server in host and guest (guest may already be destroyed)

    :param test: libvirt test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment
    """

    def kill_and_check(test, vm_name):
        """
        Destroy VM using virsh and check if it's properly destroyed.

        :param test: test object
        :param vm_name: name of the VM to destroy
        """
        test.log.info("Attempting to destroy VM: %s", vm_name)
        result = virsh.destroy(vm_name, **VIRSH_ARGS)
        libvirt.check_exit_status(result, False)

        # Wait and verify VM is destroyed
        timeout = 60
        if not utils_misc.wait_for(
            lambda: virsh.domstate(vm_name, **VIRSH_ARGS).stdout.strip() == "shut off",
            timeout, 1, 1,
            "Wait for VM to be destroyed"
        ):
            test.fail("VM %s is not properly destroyed after %s seconds" % (vm_name, timeout))
        test.log.info("VM %s is destroyed as expected", vm_name)

    def netperf_stress(test, params, vm):
        """
        Netperf stress test adapted for libvirt environment.

        :param test: test object
        :param params: test parameters
        :param vm: VM object
        """
        test.log.info("Setting up netperf stress test")

        n_client = utils_netperf.NetperfClient(
            vm.get_address(),
            params.get("client_path", "/var/tmp/"),
            netperf_source=os.path.join(
                data_dir.get_deps_dir("netperf"),
                params.get("netperf_client_link", "netperf-2.7.1.tar.bz2")
            ),
            client=params.get("shell_client"),
            port=params.get("shell_port"),
            username=params.get("username"),
            password=params.get("password"),
            prompt=params.get("shell_prompt"),
            linesep=params.get("shell_linesep", "\n").encode().decode("unicode_escape"),
            status_test_command=params.get("status_test_command", ""),
            compile_option=params.get("compile_option", ""),
        )

        n_server = utils_netperf.NetperfServer(
            utils_net.get_host_ip_address(params),
            params.get("server_path", "/var/tmp"),
            netperf_source=os.path.join(
                data_dir.get_deps_dir("netperf"),
                params.get("netperf_server_link", "netperf-2.7.1.tar.bz2")
            ),
            password=params.get("hostpassword", "redhat"),
            compile_option=params.get("compile_option", ""),
        )

        try:
            n_server.start()
            # Run netperf with message size defined in range.
            test_duration = params.get_numeric("netperf_test_duration", 600)
            test_protocols = params.get("test_protocol", "UDP_STREAM")
            netperf_output_unit = params.get("netperf_output_unit", "m")
            test_option = params.get("test_option", "")
            test_option += " -l %s" % test_duration
            if params.get("netperf_remote_cpu", "yes") == "yes":
                test_option += " -C"
            if params.get("netperf_local_cpu", "yes") == "yes":
                test_option += " -c"
            if netperf_output_unit in "GMKgmk":
                test_option += " -f %s" % netperf_output_unit

            t_option = "%s -t %s" % (test_option, test_protocols)
            host_ip = utils_net.get_host_ip_address(params)
            package_sizes = params.get("netperf_package_sizes", "1500")

            # Print netperf command being executed
            full_netperf_cmd = "netperf -t %s -H %s %s -- -m %s" % (
                test_protocols, host_ip, test_option, package_sizes)
            test.log.debug("=== NETPERF COMMAND EXECUTED ===")
            test.log.debug("Command: %s", full_netperf_cmd)
            test.log.debug("Protocol: %s", test_protocols)
            test.log.debug("Host IP: %s", host_ip)
            test.log.debug("Test Duration: %s seconds", test_duration)
            test.log.debug("Package Sizes: %s", package_sizes)

            n_client.bg_start(
                host_ip,
                t_option,
                params.get_numeric("netperf_para_sessions", 1),
                params.get("netperf_cmd_prefix", ""),
                package_sizes=package_sizes,
            )

            if utils_misc.wait_for(
                n_client.is_netperf_running, 10, 0, 1, "Wait netperf test start"
            ):
                test.log.info("Netperf test started successfully.")
            else:
                test.error("Cannot start netperf client.")

        finally:
            # Try to get netperf output before cleanup
            try:
                test.log.debug("=== NETPERF OUTPUT ===")
                test.log.debug("Netperf result: %s", getattr(n_client, 'result', 'No result available'))
            except Exception as e:
                test.log.debug("Could not get netperf output: %s", str(e))

            n_server.stop()
            n_server.cleanup(True)
            n_client.cleanup(True)

    def netload_kill_problem(test, vm_name, vm):
        """
        Execute network load test and attempt to kill VM during the test.

        :param test: test object
        :param vm_name: name of the VM
        :param vm: VM object
        """
        firewall_flush = params.get("firewall_flush", "service iptables stop")

        test.log.debug("Stopping firewall in guest and host")
        try:
            process.run(firewall_flush, shell=True)
        except Exception as e:
            test.log.warning("Could not stop firewall in host: %s", str(e))

        session = vm.wait_for_login(timeout=login_timeout)
        try:
            session.cmd(firewall_flush)
        except Exception as e:
            test.log.warning("Could not stop firewall in guest: %s", str(e))
        finally:
            session.close()

        try:
            test.log.info("Starting netperf stress test between host and guest")
            stress_thread = None
            wait_time = int(params.get("wait_bg_time", 60))
            vm_wait_time = int(params.get("wait_before_kill_vm", 500))

            stress_thread = utils_misc.InterruptedThread(
                netperf_stress, (test, params, vm)
            )
            stress_thread.start()

            utils_misc.wait_for(
                lambda: wait_time, 0, 1, "Wait netperf_stress test start"
            )

            test.log.info("Sleeping %s seconds before killing the VM", vm_wait_time)
            time.sleep(vm_wait_time)

            test.log.info("During netperf running, checking that we can kill VM with virsh destroy")
            kill_and_check(test, vm_name)

        finally:
            try:
                if stress_thread:
                    stress_thread.join(60)
            except Exception as e:
                test.log.warning("Error joining stress thread: %s", str(e))

    # Main test execution
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    login_timeout = params.get_numeric("login_timeout", 360)
    bk_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    # Ensure VM is running
    if not vm.is_alive():
        test.log.info("Starting VM %s", vm_name)
        virsh.start(vm_name, **VIRSH_ARGS)
    test.log.debug("Test with guest xml:%s", vm_xml.VMXML.new_from_inactive_dumpxml(vm_name))

    # Verify we can login to the VM
    session = vm.wait_for_login(timeout=login_timeout)
    session.close()

    # Execute the network load and kill test
    netload_kill_problem(test, vm_name, vm)

    # Restart the VM for cleanup
    test.log.info("Restarting VM for cleanup verification")
    virsh.start(vm_name, **VIRSH_ARGS)
    vm.verify_alive()
    vm.wait_for_login(timeout=login_timeout).close()

    test.log.info("Test completed successfully")
    bk_xml.sync()
