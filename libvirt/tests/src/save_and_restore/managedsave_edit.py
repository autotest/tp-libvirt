import logging
import os
import re
import aexpect

from avocado.utils import process

from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt

LOG = logging.getLogger('avocado.test.' + __name__)


def run(test, params, env):
    """
    Test command: virsh managedsave-edit <vm_name> [option]
    1) Prepare test environment, ensure VM is in requested state: running or paused
    2) Do managedsave for the VM
    3) Execute virsh managedsave-edit to edit xml
    4) Start VM
    5) Check the new xml of the VM and its state
    """

    def edit_vm_xml(xml_before_, xml_after_, option):
        """
        Edit XML content using virsh managedsave-edit command.
        Args:
            xml_before_ (str): Original XML content.
            xml_after_ (str): Updated XML content.
            option (str): Command-line option for managedsave-edit.
        Returns:
            None
        """
        edit_cmd = r":%s /" + xml_before_.replace("/", "\/")
        edit_cmd += "/" + xml_after_.replace("/", "\/")
        session = aexpect.ShellSession("sudo -s")

        try:
            LOG.info(f"Executing virsh managedsave-edit {vm_name} with option '{option}'...")
            if readonly:
                cmd = f"virsh -r managedsave-edit {vm_name} {option}"
            else:
                cmd = f"virsh managedsave-edit {vm_name} {option}"
            session.sendline(cmd)
            LOG.info(f"Replacing '{xml_before_}' with '{xml_after_}' in the XML file")
            session.sendline(edit_cmd)
            session.send('\x1b')
            session.send('ZZ')
            session.read_until_any_line_matches(
                patterns=['Managed save image of Domain.* XML configuration edited', 'not changed'],
                timeout=5,
                print_func=logging.debug
            )
        except (aexpect.ShellError, aexpect.ExpectError, aexpect.ShellTimeoutError) as details:
            session.close()
            if status_error:
                LOG.info("Failed to do managedsave-edit: %s", details)
                if err_msg not in str(details):
                    test.error(f"Managedsave-edit failed as expected but did not find the expected error message: '{err_msg}'")
                else:
                    LOG.info(f'Managedsave-edit failed as expected with error message: "{err_msg}"')
            else:
                test.error(f"Failed to do managedsave-edit: {details}")

    def vm_state_check():
        """
        Check domain xml and state after restore from saved image.
        Returns:
            None
        """
        cmd_re = virsh.dumpxml(vm_name, debug=True)
        if cmd_re.exit_status:
            test.fail(f"Failed to dump xml of domain '{vm_name}'")

        # The xml should contain the match_string
        xml = cmd_re.stdout.strip()
        LOG.info(f"The after domain is {xml_after}")
        end_index = xml_after.find(".qcow2'") + len(".qcow2'")
        xml_to_check = xml_after[:end_index]
        LOG.info(f"The xml to check is '{xml_to_check}'")

        if not re.search(xml_to_check, xml):
            test.fail(f"After domain restore the xml is not expected, expected '{xml_to_check}'")
        else:
            LOG.info(f'Found {xml_to_check} in the domain xml successfully!')

        dom_state = virsh.domstate(vm_name, debug=True).stdout.strip()
        LOG.info(f"VM state after restore is '{dom_state}'")
        if virsh_opt == '--running' and dom_state == 'paused':
            test.fail("The domain state is not as expected with option '--running'. Got 'paused', expected 'running'")
        elif virsh_opt == '--paused' and dom_state == "running":
            test.fail("The domain state is not as expected with option '--paused'. Got 'running', expected 'paused'")

    pre_state = params.get("pre_state")
    err_msg = params.get('err_msg', '')
    readonly = "yes" == params.get("readonly", "no")
    status_error = "yes" == params.get("status_error", "no")
    virsh_opt = params.get("virsh_opt")

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bk_xml = vmxml.copy()
    try:
        LOG.info("Preparing the VM state...")
        if not vm.is_alive():
            vm.start()
        if pre_state == "paused":
            virsh.suspend(vm_name, debug=True)
        vm_state = virsh.domstate(vm_name).stdout_text.strip()
        LOG.info(f"The VM state is '{vm_state}' before save")

        LOG.info("TEST STEP 1: Perform 'virsh managedsave' to save the vm...")
        cmd_result = virsh.managedsave(vm_name, debug=True)
        libvirt.check_result(cmd_result)

        LOG.info("TEST STEP 2: Run managedsave-edit to update the disk path:")
        disk_path = vm.get_first_disk_devices()['source']
        dirname = os.path.dirname(disk_path)
        basename = os.path.basename(disk_path)
        filename, ext = os.path.splitext(basename)
        new_filename = f"{filename}-test{ext}"
        new_path = os.path.join(dirname, new_filename)

        # Ensure the new path does not already exist
        if os.path.exists(new_path):
            LOG.warning(f"The path '{new_path}' already exists. Removing it...")
            os.remove(new_path)

        process.run(f"cp {disk_path} {new_path}", shell=True)
        LOG.info("The original disk path is '%s'", disk_path)
        xml_before = f"<source file='{disk_path}'/>"
        LOG.info("The original XML content is: %s", xml_before)

        xml_after = f"<source file='{new_path}'/>"
        LOG.info("The updated XML content will be: %s", xml_after)

        edit_vm_xml(xml_before, xml_after, virsh_opt)

        if not status_error:
            LOG.info("TEST STEP 3: Start VM for positive scenarios:")
            cmd_result = virsh.start(vm_name, debug=True)
            libvirt.check_result(cmd_result)
            LOG.info("TEST STEP 4: Verify VM status and the XML content for positive scenarios:")
            vm_state_check()
    finally:
        # Cleanup
        LOG.info(f"Remove the file {new_path}")
        os.remove(new_path)
        virsh.managedsave_remove(vm_name, debug=True)
        bk_xml.sync()
