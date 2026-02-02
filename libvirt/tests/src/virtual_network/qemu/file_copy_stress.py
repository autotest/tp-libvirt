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
    login_timeout = params.get_numeric("login_timeout", 360)
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)
    if not vm.is_alive():
        vm.start()
    test.log.debug("Test with guest xml:%s", vm_xml.VMXML.new_from_dumpxml(vm_name))

    test.log.info("Login to guest")
    session = vm.wait_for_login(timeout=login_timeout)

    scp_sessions = params.get_numeric("scp_para_sessions", 1)

    try:
        stress_timeout = params.get_numeric("stress_timeout", "3600")
        test.log.info("Do file transfer between host and guest")
        stop_time = time.time() + stress_timeout
        # here when set a run flag, when other case call this case as a
        # subprocess backgroundly, can set this run flag to False to stop
        # the stress test.
        env["file_transfer_run"] = True
        while time.time() < stop_time:
            scp_threads = []
            for _ in range(scp_sessions):
                scp_threads.append((utils_test.run_file_transfer, (test, params, env)))
            utils_misc.parallel(scp_threads)

    finally:
        env["file_transfer_run"] = False
        if session:
            session.close()
