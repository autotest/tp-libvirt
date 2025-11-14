import time

from virttest import utils_misc, utils_test
from virttest.libvirt_xml import vm_xml


def run(test, params, env):
    """
    Transfer a file back and forth between host and guest.

    1) Boot up a VM.
    2) Create a large file by dd on host.
    3) Copy this file from host to guest.
    4) Copy this file from guest to host.
    5) Check if file transfers ended good.

    :param test: libvirt test object.
    :param params: Dictionary with the test parameters.
    :param env: Dictionary with test environment.
    """
    login_timeout = int(params.get("login_timeout", 360))
    vm = env.get_vm(params["main_vm"])
    vm_name = params.get('main_vm')
    if not vm.is_alive():
        vm.start()
    test.log.debug("Test with guest xml:%s", vm_xml.VMXML.new_from_inactive_dumpxml(vm_name))

    test.log.info("Login to guest")
    session = vm.wait_for_login(timeout=login_timeout)

    scp_sessions = int(params.get("scp_para_sessions", 1))

    try:
        stress_timeout = float(params.get("stress_timeout", "3600"))
        test.log.info("Do file transfer between host and guest")
        start_time = time.time()
        stop_time = start_time + stress_timeout
        # here when set a run flag, when other case call this case as a
        # subprocess backgroundly, can set this run flag to False to stop
        # the stress test.
        env["file_transfer_run"] = True
        while env["file_transfer_run"] and time.time() < stop_time:
            scp_threads = []
            for index in range(scp_sessions):
                scp_threads.append((utils_test.run_file_transfer, (test, params, env)))
            utils_misc.parallel(scp_threads)

    finally:
        env["file_transfer_run"] = False
        if session:
            session.close()
