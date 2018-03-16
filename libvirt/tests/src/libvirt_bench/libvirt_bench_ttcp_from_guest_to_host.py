import time
import logging

import aexpect

from avocado.utils import path

from virttest import utils_net
from virttest import remote
from virttest import utils_misc


def run(test, params, env):
    """
    Test steps:

    1) Check the environment and get the params from params.
    2) while(loop_time < timeout):
            ttcp command.
    3) clean up.
    """
    # Find the ttcp command.
    try:
        path.find_command("ttcp")
    except path.CmdNotFoundError:
        test.cancel("Not find ttcp command on host.")
    # Get VM.
    vms = env.get_all_vms()
    for vm in vms:
        session = vm.wait_for_login()
        status, _ = session.cmd_status_output("which ttcp")
        if status:
            test.cancel("Not find ttcp command on guest.")
    # Get parameters from params.
    timeout = int(params.get("LB_ttcp_timeout", "300"))
    ttcp_server_command = params.get("LB_ttcp_server_command",
                                     "ttcp -s -r -v -D -p5015")
    ttcp_client_command = params.get("LB_ttcp_client_command",
                                     "ttcp -s -t -v -D -p5015 -b65536 -l65536 -n1000 -f K")

    host_session = aexpect.ShellSession("sh")

    try:
        current_time = int(time.time())
        end_time = current_time + timeout
        # Start the loop from current_time to end_time.
        while current_time < end_time:
            for vm in vms:
                session = vm.wait_for_login()
                host_session.sendline(ttcp_server_command)

                cmd = ("%s %s" % (ttcp_client_command, utils_net.get_host_ip_address(params)))

                def _ttcp_good():
                    status, output = session.cmd_status_output(cmd)
                    logging.debug(output)
                    if status:
                        return False
                    return True

                if not utils_misc.wait_for(_ttcp_good, timeout=60):
                    status, output = session.cmd_status_output(cmd)
                    if status:
                        test.fail("Failed to run ttcp command on guest.\n"
                                  "Detail: %s." % output)
                remote.handle_prompts(host_session, None, None, r"[\#\$]\s*$")
                current_time = int(time.time())
    finally:
        # Clean up.
        host_session.close()
        session.close()
