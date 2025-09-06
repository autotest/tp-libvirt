from virttest import virsh
from virttest import libvirt_version

from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml


def run(test, params, env):
    """
    Test set-user-sshkeys command
    """
    vm_name = params.get("main_vm")
    guest_user = params.get("guest_user")
    unprivileged_user = params.get('unprivileged_user')
    start_qemu_ga = ("no" == params.get("no_start_qemu_ga", "no"))
    status_error = ("yes" == params.get("status_error", "no"))
    uri = params.get("virsh_uri", None)
    acl_test = ("yes" == params.get("acl_test", "no"))

    libvirt_version.is_libvirt_feature_supported(params)

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()
    try:
        fail_patterns = []
        if status_error:
            if not start_qemu_ga:
                fail_patterns.append(r"QEMU guest agent is not connected")
            if acl_test:
                fail_patterns.append(r"error: access denied")

        vm.prepare_guest_agent(start=start_qemu_ga)

        res = virsh.get_user_sshkeys(vm_name, guest_user,
                                     ignore_status=True, debug=True,
                                     unprivileged_user=unprivileged_user,
                                     uri=uri)
        libvirt.check_result(res, expected_fails=fail_patterns)
    finally:
        backup_xml.sync()
