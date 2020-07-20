import uuid
import aexpect

from virttest import virsh
from virttest import libvirt_version
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Test domfsfreeze command, make sure that all supported options work well

    Test scenaries:
    1. fsfreeze all fs without options
    2. fsfreeze a mountpoint with --mountpoint
    3. fsfreeze a mountpoint without --mountpoint
    """

    def check_freeze(session):
        """
        Check whether file system has been frozen by touch a test file
        and see if command will hang.

        :param session: Guest session to be tested.
        """
        try:
            output = session.cmd_output('touch freeze_test',
                                        timeout=10)
            test.fail("Failed to freeze file system. "
                      "Create file succeeded:%s\n" % output)
        except aexpect.ShellTimeoutError:
            pass

    if not virsh.has_help_command('domfsfreeze'):
        test.cancel("This version of libvirt does not support "
                    "the domfsfreeze test")

    if not libvirt_version.version_compare(1, 1, 1):
        if params.get('setup_libvirt_polkit') == 'yes':
            test.cancel("API acl test not supported in current"
                        " libvirt version.")

    vm_ref = params.get("vm_ref", "name")
    vm_name = params.get("main_vm", "virt-tests-vm1")
    mountpoint = params.get("domfsfreeze_mnt", None)
    options = params.get("domfsfreeze_options", "")
    uri = params.get("virsh_uri")
    unprivileged_user = params.get('unprivileged_user')
    start_vm = ("yes" == params.get("start_vm", "yes"))
    has_channel = ("no" == params.get("no_qemu_ga", "no"))
    start_qemu_ga = ("no" == params.get("no_start_qemu_ga", "no"))
    status_error = ("yes" == params.get("status_error", "no"))

    # Do backup for origin xml
    xml_backup = vm_xml.VMXML.new_from_dumpxml(vm_name)
    try:
        vm = env.get_vm(vm_name)
        if vm.is_alive():
            vm.destroy()

        if has_channel:
            # Add channel device for qemu-ga
            vm.prepare_guest_agent(start=start_qemu_ga)
        else:
            # Remove qemu-ga channel
            vm.prepare_guest_agent(channel=False, start=False)

        if start_vm:
            if not vm.is_alive():
                vm.start()
            domid = vm.get_id()
            session = vm.wait_for_login()
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
        if not result.exit_status:
            check_freeze(session)

    finally:
        # Do domain recovery
        xml_backup.sync()
