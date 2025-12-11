import os

from virttest import error_context as error
from virttest import utils_misc, utils_net, utils_test
from virttest.libvirt_xml import vm_xml


@error.context_aware
def run(test, params, env):
    """
    Test nic driver in promisc mode:

    1) Boot up a VM.
    2) Repeatedly enable/disable promiscuous mode in guest.
    3) Transfer file between host and guest during nic promisc on/off

    :param test: QEMU test object.
    :param params: Dictionary with the test parameters.
    :param env: Dictionary with test environment.
    """

    def set_nic_promisc_onoff(session):
        if os_type == "linux":
            session.cmd_output_safe("ip link set dev %s promisc on" % ethname)
            session.cmd_output_safe("ip link set dev %s promisc off" % ethname)
        else:
            win_script_dest_path = params.get("win_script_dest_path")
            cmd = os.path.join(win_script_dest_path, "set_win_promisc.py")
            session.cmd(cmd)

    error.context("Boot vm and prepare test environment", test.log.info)
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    test.log.debug("vm xml:\n%s", vm_xml.VMXML.new_from_dumpxml(vm_name))
    vm.verify_alive()

    timeout = params.get_numeric("login_timeout")
    session_serial = vm.wait_for_serial_login(timeout=timeout)
    session = vm.wait_for_login(timeout=timeout)

    os_type = params.get("os_type")
    if os_type == "linux":
        ethname = utils_net.get_linux_ifname(session, vm.get_mac_address(0))
    else:
        script_path = os.path.join(test.virtdir, "scripts/set_win_promisc.py")
        win_script_dest_path = params.get("win_script_dest_path")
        vm.copy_files_to(script_path, win_script_dest_path)

    try:
        transfer_thread = utils_misc.InterruptedThread(
            utils_test.run_file_transfer, (test, params, env)
        )

        error.context("Run utils_test.file_transfer ...", test.log.info)
        transfer_thread.start()

        error.context(
            "Perform file transfer while turning nic promisc on/off", test.log.info
        )
        while transfer_thread.is_alive():
            set_nic_promisc_onoff(session_serial)
    except Exception:
        transfer_thread.join(suppress_exception=True)
        raise
    else:
        transfer_thread.join()
    finally:
        if session:
            session.close()
        if session_serial:
            session_serial.close()
