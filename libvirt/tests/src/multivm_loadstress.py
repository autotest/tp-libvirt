import logging
import time

from avocado.core import exceptions
from virttest import utils_test


def run(test, params, env):
    """
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
    stress_type = params.get("stress_type", "uperf")
    error = False
    if stress_type == "uperf":
        server_client = utils_test.UperfStressload(params, env)
    elif stress_type == "netperf":
        server_client = utils_test.NetperfStressload(params, env)
    else:
        logging.error("Client server stress tool %s not defined", stress_type)
        exceptions.TestFail("%s run failed: see error messages above" % stress_type)

    if server_client.load_stress(params):
        error = True
    if stress_duration and not error:
        time.sleep(stress_duration)
    if not error:
        if server_client.verify_unload_stress(params):
            error = True
    if error:
        raise exceptions.TestFail("%s run failed: see error messages above" % stress_type)
