import re
import uuid
from autotest.client.shared import error
from virttest import virsh
from virttest.libvirt_xml import vm_xml


def run(test, params, env):
    """
    Test domtime command, make sure that all supported options work well

    Test scenaries:
    1. domtime to get time
    2. domtime with wrong options

    Notice: set time has not been supported, so not tests here.
    """

    if not virsh.has_help_command('domtime'):
        raise error.TestNAError("This version of libvirt does not support "
                                "the domtime test")

    vm_ref = params.get("vm_ref", "name")
    vm_name = params.get("main_vm", "virt-tests-vm1")
    start_vm = ("yes" == params.get("start_vm", "yes"))
    has_qemu_ga = not ("yes" == params.get("no_qemu_ga", "no"))
    start_qemu_ga = not ("yes" == params.get("no_start_qemu_ga", "no"))
    status_error = ("yes" == params.get("status_error", "no"))
    domtime_opts = params.get("domtime_options", "")
    pretty_opt = ("yes" == params.get("domtime_pretty", "no"))
    get_time = ("yes" == params.get("domtime_get", "no"))
    sync_opt = ("yes" == params.get("domtime_sync", "no"))
    now_opt = ("yes" == params.get("domtime_now", "no"))
    time_opt = params.get("domtime_time", None)

    # Do backup for origin xml
    xml_backup = vm_xml.VMXML.new_from_dumpxml(vm_name)
    try:
        vm = env.get_vm(vm_name)

        vm.destroy()

        if has_qemu_ga:
            vm.prepare_guest_agent(start=start_qemu_ga)
        else:
            # Remove qemu-ga channel
            vm.prepare_guest_agent(channel=has_qemu_ga, start=False)

        if start_vm:
            if not vm.is_alive():
                vm.start()
            domid = vm.get_id()
        else:
            vm.destroy()

        domuuid = vm.get_uuid()

        if vm_ref == "id":
            vm_ref = domid
        elif vm_ref == "uuid":
            vm_ref = domuuid
        elif vm_ref.count("invalid"):
            vm_ref = uuid.uuid1()
        elif vm_ref == "none":
            vm_ref = ""
        elif vm_ref == "name":
            vm_ref = vm_name

        # Execute domtime command
        cmd_result = virsh.domtime(vm_ref, now=now_opt, pretty=pretty_opt,
                                   sync=sync_opt, time=time_opt,
                                   options=domtime_opts, debug=True)

        if not status_error:
            if cmd_result.exit_status != 0:
                raise error.TestFail("Fail to do virsh domtime, error %s" %
                                     cmd_result.stderr)
            # Prove the time format
            if get_time:
                if pretty_opt:
                    date = cmd_result.stdout.split()[1]
                    time = cmd_result.stdout.split()[2]
                    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date) or \
                       not re.match(r"^[0-2][0-9]:[0-5][0-9]:[0-5][0-9]$",
                                    time):
                        raise error.TestFail("Return pretty time is error: %s" %
                                             cmd_result.stdout)

                else:
                    time = cmd_result.stdout.split()[1]
                    if not time.isdigit():
                        raise error.TestFail("Return time is error: %s" %
                                             cmd_result.stdout)
        else:
            if cmd_result.exit_status == 0:
                raise error.TestFail("Command 'virsh domtime' failed ")

    finally:
        # Do domain recovery
        xml_backup.sync()
