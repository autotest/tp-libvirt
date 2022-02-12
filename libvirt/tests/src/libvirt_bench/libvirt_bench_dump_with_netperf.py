import os
import logging as log

from virttest import utils_test
from virttest import utils_misc
from virttest import data_dir


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test steps:

    1) Get the params from params.
    2) Run netperf on guest.
    3) Dump each VM and check result.
    3) Clean up.
    """
    vms = env.get_all_vms()
    netperf_control_file = params.get("netperf_controle_file",
                                      "netperf.control")
    # Run netperf on guest.
    guest_netperf_bts = []
    params["test_control_file"] = netperf_control_file
    # Fork a new process to run netperf on each guest.
    for vm in vms:
        params["main_vm"] = vm.name
        control_path = os.path.join(test.virtdir, "control",
                                    netperf_control_file)
        session = vm.wait_for_login()
        bt = utils_test.BackgroundTest(utils_test.run_autotest,
                                       [vm, session, control_path,
                                        None, None, params])
        bt.start()
        guest_netperf_bts.append(bt)

    for vm in vms:
        session = vm.wait_for_login()

        def _is_netperf_running():
            return (not session.cmd_status(
                    "cat /usr/local/autotest/results/default/debug/client.DEBUG|"
                    "grep \"seconds remaining\""))
        if not utils_misc.wait_for(_is_netperf_running, timeout=120):
            test.cancel("Failed to run netperf in guest.\n"
                        "Since we need to run a autotest of netperf "
                        "in guest, so please make sure there are some "
                        "necessary packages in guest, such as gcc, tar, bzip2")

    logging.debug("Netperf is already running in VMs.")

    try:
        dump_path = os.path.join(data_dir.get_tmp_dir(), "dump_file")
        for vm in vms:
            vm.dump(dump_path)
            # Check the status after vm.dump()
            if not vm.is_alive():
                test.fail("VM is shutoff after dump.")
            if vm.wait_for_shutdown():
                test.fail("VM is going to shutdown after dump.")
            # Check VM is running normally.
            vm.wait_for_login()
    finally:
        # Destroy VM.
        for vm in vms:
            vm.destroy()
        for bt in guest_netperf_bts:
            bt.join(ignore_status=True)
