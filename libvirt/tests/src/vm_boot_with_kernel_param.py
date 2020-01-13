import logging

from virttest.libvirt_xml import vm_xml
from virttest import utils_test
from virttest import cpu
from virttest.utils_test.libvirt import check_status_output


def check_cmdline(session, expected):
    """
    Checks if guest system was started with expected parameter.
    Fails test otherwise.
    :param expected: Expected substring in kernel cmdline
    :param session: active guest session
    """

    cmd = 'cat /proc/cmdline'
    status, output = session.cmd_status_output(cmd)
    check_status_output(status, output, expected_match=expected)


def run(test, params, env):
    """
    Test to change the kernel param based on user input.

    1. Prepare test environment, boot the guest
    2. Change the kernel parameter as per user input
    3. Reboot the guest and check whether /proc/cmdline reflects
    4. Check the boot log in guest dmesg and validate
    5. Perform any test operation if any, based on kernel param change
    6. Recover test environment
    """
    vms = params.get("vms").split()
    kernel_param = params.get("kernel_param", "quiet")
    kernel_param_remove = params.get("kernel_param_remove", "")
    if not kernel_param:
        kernel_param = None
    if not kernel_param_remove:
        kernel_param_remove = None
    cpu_check = params.get("hardware", "").upper()
    boot_log = params.get("boot_log", None)
    check_cmdline_only = "yes" == params.get("check_cmdline_only", "no")
    status_error = params.get("status_error", "no") == "yes"
    vm_dict = {}
    vm_list = env.get_all_vms()
    # To ensure host that doesn't support Radix MMU gets skipped
    if cpu_check:
        cpu_model = cpu.get_cpu_info()['Model name'].upper()
        if cpu_check not in cpu_model:
            logging.info("This test will work for %s", cpu_check)
            test.skip("Test is not applicable for %s" % cpu_model)
    # back up vmxml
    for vm_name in vms:
        vm_dict[vm_name] = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    try:
        for vm in vm_list:
            session = vm.wait_for_login()
            if check_cmdline_only:
                check_cmdline(session, params.get("expect_in_cmdline",
                                                  "expect_in_cmdline not defined in test configuration"))
            else:
                utils_test.update_boot_option(vm, args_added=kernel_param,
                                              args_removed=kernel_param_remove,
                                              need_reboot=True)
            if boot_log:
                session = vm.wait_for_login()
                # To ensure guest that doesn't support Radix MMU gets skipped
                if cpu_check:
                    cmd = "grep cpu /proc/cpuinfo | awk '{print $3}' | "
                    cmd += "head -n 1"
                    status, output = session.cmd_status_output(cmd)
                    if status:
                        test.error("couldn't get cpu information from guest "
                                   "%s" % vm.name)
                    if cpu_check not in output.upper() and "radix" in boot_log:
                        test.skip("radix MMU not supported in %s" % output)
                status, output = session.cmd_status_output("dmesg")
                if status:
                    logging.error(output)
                    test.error("unable to get dmesg from guest: %s" %
                               vm.name)
                if status_error:
                    if boot_log in output:
                        test.fail("Able to find %s in dmesg of guest: "
                                  "%s" % (boot_log, vm.name))
                    logging.info("unable to find %s in dmesg of guest: %s",
                                 boot_log, vm.name)
                else:
                    if boot_log not in output:
                        test.fail("unable to find %s in dmesg of guest: "
                                  "%s" % (boot_log, vm.name))
                    logging.info("Able to find %s in dmesg of guest: %s",
                                 boot_log, vm.name)
            if session:
                session.close()
    finally:
        # close the session and recover the vms
        if session:
            session.close()
        for vm in vm_list:
            vm.destroy()
            vm_dict[vm.name].sync()
