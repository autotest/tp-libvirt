import uuid
from autotest.client.shared import error
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from provider import libvirt_version


def run(test, params, env):
    """
    Test domfsfreeze command, make sure that all supported options work well

    Test scenaries:
    1. fsfreeze all fs without options
    2. fsfreeze a mountpoint with --mountpoint
    3. fsfreeze a mountpoint without --mountpoint
    """

    if not virsh.has_help_command('domfsfreeze'):
        raise error.TestNAError("This version of libvirt does not support "
                                "the domfsfreeze test")

    vm_ref = params.get("vm_ref", "name")
    vm_name = params.get("main_vm", "virt-tests-vm1")
    start_vm = ("yes" == params.get("start_vm", "yes"))
    has_qemu_ga = not ("yes" == params.get("no_qemu_ga", "no"))
    start_qemu_ga = not ("yes" == params.get("no_start_qemu_ga", "no"))
    mountpoint = params.get("domfsfreeze_mnt", None)
    status_error = ("yes" == params.get("status_error", "no"))
    options = params.get("domfsfreeze_options", "")
    uri = params.get("virsh_uri")
    unprivileged_user = params.get('unprivileged_user')

    if unprivileged_user:
        if unprivileged_user.count('EXAMPLE'):
            unprivileged_user = 'testacl'

    if not libvirt_version.version_compare(1, 1, 1):
        if params.get('setup_libvirt_polkit') == 'yes':
            raise error.TestNAError("API acl test not supported in current"
                                    " libvirt version.")

    # Do backup for origin xml
    xml_backup = vm_xml.VMXML.new_from_dumpxml(vm_name)
    try:
        vm = env.get_vm(vm_name)
        vm.destroy()

        if has_qemu_ga:
            # Add channel device for qemu-ga
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

        result = virsh.domfsfreeze(vm_ref, mountpoint=mountpoint,
                                   options=options,
                                   unprivileged_user=unprivileged_user,
                                   uri=uri, debug=True)
        libvirt.check_exit_status(result, status_error)

    finally:
        # Do domain recovery
        xml_backup.sync()
