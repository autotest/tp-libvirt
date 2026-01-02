from virttest import data_dir
from virttest import utils_net
from virttest.libvirt_xml import vm_xml


def run(test, params, env):
    """
    Test net adapter after set NetAdapterrss, this case will:

    1) boot up guest
    2) set NetAdapterrss
    3) use speedtest trigger some network traffic

    :param test: QEMU test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environmen.
    """

    def execute_command(command, timeout=60, omit=False):
        """
        Execute command and return the output
        """
        test.log.info("Sending command: %s", command)
        status, output = session.cmd_status_output(command, timeout)
        if status != 0 and omit is False:
            test.error("execute command fail: %s" % output)
        return output

    vm = env.get_vm(params["main_vm"])
    vm.verify_alive()
    test.log.debug("vm xml:\n%s", vm_xml.VMXML.new_from_dumpxml(vm.name))
    session = vm.wait_for_serial_login(timeout=360)

    # Copy speedtest to guest
    speedtest_host_path = data_dir.get_deps_dir("speedtest")
    dst_path = params["dst_path"]
    test.log.info("Copy Speedtest to guest.")
    s, o = session.cmd_status_output("mkdir %s" % dst_path)
    if s and "already exists" not in o:
        test.error(
            "Could not create Speedtest directory in "
            "VM '%s', detail: '%s'" % (vm.name, o)
        )
    vm.copy_files_to(speedtest_host_path, dst_path)

    # set up adapterrss in guest
    mac = vm.get_mac_address()
    ifname = utils_net.get_windows_nic_attribute(session, "macaddress", mac, "netconnectionid")
    set_adapterrss_cmd = params["set_adapterrss_cmd"]
    execute_command(set_adapterrss_cmd % ifname)

    # start test with speedtest
    speedtest_path_cmd = params["speedtest_path_cmd"]
    set_license_cmd = params["set_license_cmd"]
    start_test_cmd = params["start_test_cmd"]
    execute_command(speedtest_path_cmd)
    execute_command(set_license_cmd, omit=True)
    for _i in range(10):
        output = execute_command(start_test_cmd, omit=True)
        test.log.info(output)
