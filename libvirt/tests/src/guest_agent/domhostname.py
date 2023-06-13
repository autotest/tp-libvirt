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

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()

    try:
        vm.prepare_guest_agent()

        test.log.info(f"TEST_STEP: Set VM's hostname to {expr_hostname}.")
        vm_session = vm.wait_for_login()
        vm_session.cmd("hostname %s" % expr_hostname)
        domain_ref = vm.get_id() if params.get("domain_ref") == 'id' \
            else eval('vm.%s' % params.get("domain_ref"))

        test.log.info(f"TEST_STEP: Get VM's hostname via guest agent.")
        res = virsh.domhostname(domain_ref, debug=True)
        libvirt.check_result(res, expected_match=expr_hostname)
    finally:
        backup_xml.sync()
