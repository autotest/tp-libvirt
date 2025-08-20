from virttest import virsh

from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml


def run(test, params, env):
    """
    Get domain's hostname via guest agent
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    expr_hostname = params.get("expr_hostname", "myhostname")
    status_error = params.get("status_error", "no") == "yes"
    start_qemu_ga = params.get("no_start_qemu_ga", "no") == "no"

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()

    try:
        vm.prepare_guest_agent(start=start_qemu_ga)

        if not status_error:
            test.log.info(f"TEST_STEP: Set VM's hostname to {expr_hostname}.")
            vm_session = vm.wait_for_login()
            vm_session.cmd("hostname %s" % expr_hostname)

        domain_ref = vm.get_id() if params.get("domain_ref") == 'id' \
            else eval('vm.%s' % params.get("domain_ref", "name"))

        test.log.info(f"TEST_STEP: Get VM's hostname via guest agent.")
        res = virsh.domhostname(domain_ref, ignore_status=True, debug=True)

        fail_patterns = []
        if status_error:
            if not start_qemu_ga:
                fail_patterns.append(r"QEMU guest agent is not connected")
            libvirt.check_result(res, expected_fails=fail_patterns)
        else:
            libvirt.check_result(res, expected_match=expr_hostname)
    finally:
        backup_xml.sync()
