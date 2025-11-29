import re

from virttest import utils_net
from virttest import utils_test

from virttest.libvirt_xml import vm_xml


def run(test, params, env):
    """
    Boot guest with different vectors, then do file transfer tests.

    1) Boot up VM with certain vectors.
    2) Check guest msi & queues info
    3) Start 10 scp file transfer tests

    :param test: QEMU test object.
    :param params: Dictionary with the test parameters.
    :param env: Dictionary with test environment.
    """

    login_timeout = params.get_numeric("login_timeout", 360)
    cmd_timeout = params.get_numeric("cmd_timeout", 240)

    # boot the vm with the queues
    queues = params.get_numeric("queues_nic1")
    vm_name = params["main_vm"]
    vm = env.get_vm(vm_name)
    if not vm.is_alive():
        vm.start()
    test.log.debug("Boot guest with queues:%s, and xml is %s" % (queues, vm_xml.VMXML.new_from_dumpxml(vm_name)))
    session = vm.wait_for_login(timeout=login_timeout)

    if params["os_type"] == "linux":
        nic = vm.virtnet[0]
        ifname = utils_net.get_linux_ifname(session, nic.mac)
        _, output = session.cmd_status_output("ethtool -l %s" % ifname)
        if not re.search("Combined:.*?%s" % queues, output):
            test.fail("Guest ethtool shows unexpected combined queues. "
                      "Expected: %s, but ethtool output: %s" % (queues, output))

        # check the msi for linux guest
        test.log.debug("Check the msi number in guest")
        devices = session.cmd_output(
            "lspci | grep Ethernet", timeout=cmd_timeout, safe=True
        ).strip()
        for device in devices.split("\n"):
            if not device:
                continue
            d_id = device.split()[0]
            msi_check_cmd = params["msi_check_cmd"] % d_id
            _, output = session.cmd_status_output(
                msi_check_cmd, timeout=cmd_timeout, safe=True
            )
            find_result = re.search(r" MSI-X:\s*Enable\+\s*Count=(\d+) ", output)
            if not find_result:
                test.fail("No MSI info in output: %s" % output)
            msis = int(find_result.group(1))
            if msis != 2 * queues + 2:
                test.fail("Expected MSI count: %s, but got: %s" % (2 * queues + 2, msis))
    else:
        # verify driver
        test.log.debug("Check if the driver is installed and verified")
        driver_name = params.get("driver_name", "netkvm")
        session = utils_test.qemu.windrv_check_running_verifier(
            session, vm, test, driver_name, cmd_timeout
        )
        # check the msi for windows guest with trace view
        test.log.debug("Check the msi number in guest")
        msis, cur_queues = utils_net.get_msis_and_queues_windows(params, vm)
        if cur_queues != queues or msis != 2 * queues + 2:
            test.fail("queues not correct with %s, expect %s" % (cur_queues, queues))

    # start scp test
    test.log.debug("Start scp file transfer test")
    scp_count = params.get_numeric("scp_count", 10)
    for _ in range(scp_count):
        utils_test.run_file_transfer(test, params, env)
    if session:
        session.close()
