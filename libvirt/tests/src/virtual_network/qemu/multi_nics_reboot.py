import time

from virttest import utils_test

from virttest.libvirt_xml import vm_xml


def run(test, params, env):
    """
    Qemu reboot test:
    1) Log into a guest
    3) Send a reboot command or a system_reset monitor command (optional)
    4) Wait until the guest is up again
    5) Log into the guest to verify it's up again

    :param test: QEMU test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """

    timeout = params.get_numeric("login_timeout", 240)
    serial_login = params.get_boolean("serial_login", "no")
    vms = env.get_all_vms()
    for vm in vms:
        vm_name = vm.name
        test.log.debug("The guest:%s xml is %s", vm_name, vm_xml.VMXML.new_from_inactive_dumpxml(vm_name))
        vm.cleanup_serial_console()
        vm.create_serial_console()
        session = vm.wait_for_serial_login(timeout=timeout)
        session.close()

    if params.get("rh_perf_envsetup_script"):
        for vm in vms:
            if serial_login:
                session = vm.wait_for_serial_login(timeout=timeout)
            else:
                session = vm.wait_for_login(timeout=timeout)
            utils_test.service_setup(vm, session, test.virtdir)
            session.close()

    if params.get("reboot_method"):
        for vm in vms:
            test.log.debug("Reboot guest '%s'." % vm.name)
            if params.get("reboot_method") == "system_reset":
                time.sleep(params.get_numeric("sleep_before_reset", 10))
            # Reboot the VM
            if serial_login:
                session = vm.wait_for_serial_login(timeout=timeout)
            else:
                session = vm.wait_for_login(timeout=timeout)
            for _ in range(params.get_numeric("reboot_count", 1)):
                session = vm.reboot(
                    session, params.get("reboot_method"), 0, timeout, serial_login
                )
            session.close()
