import logging
import time
import shutil
import os
from avocado.core import exceptions
from virttest.utils_iptables import Iptables
from virttest import data_dir
from virttest import utils_test


def run(test, params, env):
    """
    :param test:   kvm test object
    :param params: Dictionary with the test parameters
    :param env:    Dictionary with test environment.
    The test will
    1. Create 'n' uperf server-client pairs,
    2. Configure iptables in guest and install uperf
    3. Kick start uperf run, wait for duration [customized given in uperf profile]
    4. After run, test will check for errors and make sure all guests are reachable.
    5. Cleanup temp files and iptable rules added before
    """

    stress_duration = int(params.get("stress_duration", "20"))
    ip_rule = params.get("ip_rule", "")
    stress_type = params.get("stress_type", "")
    need_profile = int(params.get("need_profile", 0))
    server_cmd = params.get("%s_server_cmd" % stress_type)
    client_cmd = params.get("%s_client_cmd" % stress_type)
    client_vms = []
    server_vms = []
    error = 0
    vms = env.get_all_vms()

    if (need_profile):
        profile = params.get("client_profile_%s" % stress_type)
        profile = os.path.join(data_dir.get_root_dir(), profile)
        profile_pattren = params.get("profile_pattren", "").split()

    for index, vm in enumerate(vms):
        if index % 2 != 0:
            # Process for client
            client_vms.append(vm)
        else:
            # Process for server
            server_vms.append(vm)
    pair_vms = zip(server_vms, client_vms)

    if len(server_vms) != len(client_vms):
        test.cancel("This test requires server and client vms in 1:1 ratio")

    if stress_type == "uperf":
        protocol = params.get("%s_protocol" % stress_type, "tcp")
        nthreads = params.get("nthreads", "32")
        client_cmd = client_cmd % os.path.basename(profile)
        if not profile.endswith(".xml"):
            logging.debug("Error: profile should be an xml: %s", profile)
            test.cancel("%s profile not valid", stress_type)
        profile_values = [nthreads, str(stress_duration), protocol]
        if len(profile_pattren) != len(profile_values):
            test.cancel("Profile pattrens not matching values passed: fix the cfg file with right pattren")
        profile_pattren.append('serverip')
        pat_repl = dict(zip(profile_pattren, profile_values))
    elif stress_type == "netperf":
        ports = params.get("ports", "16604")
        test_protocol = params.get("test_protocols", "TCP_STREAM")
        server_cmd = server_cmd.format(ports)
        client_cmd = client_cmd.format("{0}", ports, stress_duration, test_protocol)
    else:
        raise NotImplementedError

    for server_vm, client_vm in pair_vms:
        try:
            params['stress_cmds_%s' % stress_type] = server_cmd
            stress_server = utils_test.VMStress(server_vm, stress_type, params)
            params['server_pwd'] = params.get("password")
            # wait so that guests get ip address, else get_address will fail
            client_vm.wait_for_login().close()
            if ip_rule:
                for vm_ip in [server_vm.get_address(), client_vm.get_address()]:
                    params['server_ip'] = vm_ip
                    Iptables.setup_or_cleanup_iptables_rules(
                        [ip_rule], params=params, cleanup=False)
            stress_server.load_stress_tool()
            if need_profile:
                profile_backup = profile + '.backup'
                shutil.copy(profile, profile_backup)
                pat_repl.update({"serverip": str(server_vm.get_address())})
                utils_test.prepare_profile(test, profile, pat_repl)
                client_vm.copy_files_to(profile, "/home", timeout=60)
                shutil.copy(profile_backup, profile)
                os.remove(profile_backup)
            else:
                client_cmd = client_cmd.format(str(server_vm.get_address()))
            params['stress_cmds_%s' % stress_type] = client_cmd
            stress_client = utils_test.VMStress(client_vm, stress_type, params)
            stress_client.load_stress_tool()
        except exceptions.TestError as err_msg:
            error = 1
            logging.error(err_msg)

    if stress_duration and not error:
        time.sleep(stress_duration)

    for vm in vms:
        try:
            s_ping, o_ping = utils_test.ping(vm.get_address(), count=10, timeout=20)
            logging.info(o_ping)
            if s_ping != 0:
                error = 1
                logging.error("%s seem to have gone out of network", vm.name)
            else:
                stress = utils_test.VMStress(vm, stress_type, params)
                stress.unload_stress()
                if ip_rule:
                    params['server_ip'] = vm.get_address()
                    Iptables.setup_or_cleanup_iptables_rules([ip_rule], params=params, cleanup=True)
                stress.clean()
                vm.verify_dmesg()
        except exceptions.TestError as err_msg:
            error = 1
            logging.error(err_msg)
        finally:
            if vm.exists() and vm.is_persistent():
                vm.undefine()
    if error:
        test.fail("%s run failed: see error messages above" % stress_type)
