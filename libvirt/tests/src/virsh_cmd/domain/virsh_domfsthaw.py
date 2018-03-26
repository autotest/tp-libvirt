from virttest import virsh
from virttest.libvirt_xml import vm_xml


def run(test, params, env):
    """
    Test domfsthaw command, make sure that all supported options work well

    Test scenaries:
    1. fsthaw fs which has been freezed
    2. fsthaw fs which has not been freezed

    Note: --mountpoint still not supported so will not test here
    """

    if not virsh.has_help_command('domfsthaw'):
        test.cancel("This version of libvirt does not support "
                    "the domfsthaw test")

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    start_vm = ("yes" == params.get("start_vm", "no"))
    no_freeze = ("yes" == params.get("no_freeze", "yes"))
    has_qemu_ga = not ("yes" == params.get("no_qemu_ga", "no"))
    start_qemu_ga = not ("yes" == params.get("no_start_qemu_ga", "no"))
    status_error = ("yes" == params.get("status_error", "no"))
    options = params.get("domfsthaw_options", "")
    vm_ref = params.get("vm_ref", "")

    # Do backup for origin xml
    xml_backup = vm_xml.VMXML.new_from_dumpxml(vm_name)
    try:
        vm = env.get_vm(vm_name)

        vm.destroy()

        if not vm.is_alive():
            vm.start()

        # Firstly, freeze all filesytems
        if not no_freeze:
            # Add channel device for qemu-ga
            vm.prepare_guest_agent()
            cmd_result = virsh.domfsfreeze(vm_name, debug=True)
            if cmd_result.exit_status != 0:
                test.fail("Fail to do virsh domfsfreeze, error %s" %
                          cmd_result.stderr)

        if has_qemu_ga:
            vm.prepare_guest_agent(start=start_qemu_ga)
        else:
            # Remove qemu-ga channel
            vm.prepare_guest_agent(channel=has_qemu_ga, start=False)

        if start_vm:
            if not vm.is_alive():
                vm.start()
        else:
            vm.destroy()

        if vm_ref == "none":
            vm_name = " "

        cmd_result = virsh.domfsthaw(vm_name, options=options, debug=True)
        if not status_error:
            if cmd_result.exit_status != 0:
                test.fail("Fail to do virsh domfsthaw, error %s" %
                          cmd_result.stderr)
        else:
            if cmd_result.exit_status == 0:
                test.fail("Command 'virsh domfsthaw' failed ")

    finally:
        # Do domain recovery
        xml_backup.sync()
