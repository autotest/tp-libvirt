import logging
import os
import re
import aexpect

from virttest import data_dir
from virttest import virsh
from virttest.libvirt_xml import vm_xml


def run(test, params, env):
    """
    Test command: virsh save-image-edit <file>

    1) Prepare test environment.
    2) Save a domain state to a file
    3) Execute virsh save-image-edit to edit xml in the saved
       state file
    4) Restore VM
    5) Check the new xml of the VM and its state
    """

    def edit_image_xml():
        edit_cmd = r":%s /<boot dev='hd'\/>/<boot dev='cdrom'\/>"

        if restore_state == "running":
            option = "--running"
        elif restore_state == "paused":
            option = "--paused"
        elif restore_state == "":
            option = ""
        else:
            test.fail("Unknown save-image-define option")

        session = aexpect.ShellSession("sudo -s")
        try:
            logging.info("Execute virsh save-image-edit %s %s",
                         vm_save, option)
            session.sendline("virsh save-image-edit %s %s " %
                             (vm_save, option))

            logging.info("Replace '<boot dev='hd'/>' to '<boot dev='cdrom'/>'")
            session.sendline(edit_cmd)
            session.send('\x1b')
            session.send('ZZ')
            session.read_until_any_line_matches(
                    patterns=['State file.*%s edited' % vm_save, 'not changed'],
                    timeout=5,
                    print_func=logging.debug)
        except (aexpect.ShellError, aexpect.ExpectError,
                aexpect.ShellTimeoutError) as details:
            session.close()
            test.error("Failed to do save-image-edit: %s" % details)

    def vm_state_check():
        cmd_result = virsh.dumpxml(vm_name, debug=True)
        if cmd_result.exit_status:
            test.fail("Failed to dump xml of domain %s" % vm_name)

        # The xml should contain the match_string
        xml = cmd_result.stdout.strip()
        match_string = "<boot dev='cdrom'/>"
        if not re.search(match_string, xml):
            test.fail("After domain restore the xml is not expected")

        domstate = virsh.domstate(vm_name, debug=True).stdout.strip()
        if restore_state != domstate:
            test.fail("The domain state is not expected")

    # MAIN TEST CODE ###
    # Process cartesian parameters
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vm.wait_for_login()

    restore_state = params.get("restore_state", "")
    vm_save = params.get("vm_save", "vm.save")

    vm_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    try:
        # Get a tmp_dir.
        tmp_dir = data_dir.get_tmp_dir()
        if os.path.dirname(vm_save) is "":
            vm_save = os.path.join(tmp_dir, vm_save)

        # Save the RAM state of a running domain
        cmd_result = virsh.save(vm_name, vm_save, debug=True)
        if cmd_result.exit_status:
            test.fail("Failed to save running domain %s" % vm_name)

        # Edit the xml in the saved state file
        edit_image_xml()

        # Restore domain
        cmd_result = virsh.restore(vm_save, debug=True)
        if cmd_result.exit_status:
            test.fail("Failed to restore domain %s" % vm_name)
        os.remove(vm_save)

        vm_state_check()

    finally:
        # cleanup
        vm_backup.sync()

        if restore_state == "paused":
            virsh.resume(vm_name)

        if os.path.exists(vm_save):
            virsh.restore(vm_save)
            os.remove(vm_save)
