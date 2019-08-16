import logging
import time
import re

from avocado.core import exceptions
from virttest import utils_test


def run(test, params, env):
    """
    :param params: Dictionary with the test parameters
    :param env:    Dictionary with test environment.
    The test, based on input params, will
    1. Accepts pair of server-client guests and load mentioned stress in it.
    2. Load standalone stress in other guests, if any.
    3. Configure required steps such as iptable addition, if any given.
    4. If host stress is considered, start host stress run.
    5. Kick start stress runs, wait for duration
    6. After run, test will check for errors
    7. Cleanup temp files and iptable rules added before
    """
    def clientserver_stress(server_client, opt_type):
        """
        This method would help load or unload given client server stress
        test
        """
        if opt_type == "load":
            if server_client.load_stress(server_clients_params):
                return True
        else:
            if server_client.verify_unload_stress(server_clients_params):
                return True
        return False

    def standalone_stress(stress_server, opt_type, server_vms):
        """
        This method would help load stress and unload,verify state of given vms
        post standalone server stress test
        """
        error_state = False
        if opt_type == "load":
            try:
                for vm in server_vms:
                    stress_server[vm.name].load_stress_tool()
            except exceptions.TestError as err_msg:
                logging.error(err_msg)
                return True
        else:
            for vm in server_vms:
                try:
                    s_ping, o_ping = utils_test.ping(
                        vm.get_address(), count=5, timeout=20)
                    if s_ping != 0:
                        error_state = True
                        logging.error(
                            "%s seem to have gone out of network", vm.name)
                        continue
                    uptime = vm.uptime()
                    if up_time[vm.name] > uptime:
                        error_state = True
                        logging.error(
                            "%s seem to have rebooted during the stress run", vm.name)
                    stress_server[vm.name].unload_stress()
                    stress_server[vm.name].clean()
                    vm.verify_dmesg()
                except exceptions.TestError as err_msg:
                    logging.error(err_msg)
                    error_state = True

        return error_state

    stress_duration = int(params.get("stress_duration", "20"))
    stress_config = params.get("stress_config", "client_server")
    error = False
    server_clients_params = params.object_params("server_clients")
    stress_tool = params.get("stress_tool", "")
    stress_args = params.get("%s_args" % stress_tool)
    stress_server = {}
    up_time = {}
    server_vms = []
    standalone_vms = []

    if stress_config in {"mix", "standalone"}:
        if stress_config != "standalone":
            server_clients_vms = re.split(
                '_| ', server_clients_params.get("vm_pair"))
            standalone_vms = [vm for vm in env.get_all_vms() if vm.name not in server_clients_vms]
            for vm in standalone_vms:
                server_vms.append(vm)
        else:
            server_vms = env.get_all_vms()
        for vm in server_vms:
            up_time[vm.name] = vm.uptime()
            vm_params = params.object_params(vm.name)
            stress_type = vm_params.get("stress_type", "stress-ng")
            stress_server[vm.name] = utils_test.VMStress(
                vm, stress_type, vm_params)

    if stress_config in {"mix", "server_client"}:
        if not server_clients_params:
            logging.error("No server client params defined")
            error = True
        else:
            stress_type = server_clients_params.get("stress_type", "uperf")
            if stress_type == "uperf":
                server_client = utils_test.UperfStressload(
                    server_clients_params, env)
            elif stress_type == "netperf":
                server_client = utils_test.NetperfStressload(
                    server_clients_params, env)
            else:
                logging.error(
                    "Client server stress tool %s not defined", stress_type)
                error = True
            if standalone_vms:
                server_client.vms = server_client.server_vms + server_client.client_vms

    if stress_config == "mix" and not error:
        error = clientserver_stress(server_client, "load")
        if not error:
            error = standalone_stress(stress_server, "load", server_vms)
    elif stress_config == "standalone" and not error:
        error = standalone_stress(stress_server, "load", server_vms)
    elif stress_config == "server_client" and not error:
        error = clientserver_stress(server_client, "load")
    else:
        logging.error("stress config undefined %s" % stress_config)
        error = True
    if stress_tool and not error:
        try:
            host_stress = utils_test.HostStress(stress_tool, params)
            host_stress.load_stress_tool()
        except utils_test.StressError, info:
            logging.error(info)
            error = True
    if stress_duration and not error:
        time.sleep(stress_duration)
    if not error:
        if stress_config in {"mix", "server_client"}:
            error = clientserver_stress(server_client, "unload")
        if stress_config in {"mix", "standalone"}:
            error = standalone_stress(stress_server, "unload", server_vms)
        if stress_tool:
            logging.info("unloading stress on host")
            host_stress.unload_stress()
    if error:
        raise exceptions.TestFail("Run failed: see error messages above")
