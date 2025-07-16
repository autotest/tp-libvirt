import logging
import os
import aexpect

from virttest import data_dir
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_vmxml


LOG = logging.getLogger('avocado.test.' + __name__)


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

    def edit_image_xml(xml_before_, xml_after_, option):
        """
        Edit XML content using virsh save-image-edit command.
        :param xml_before_: Original XML content.
        :param xml_after_: Updated XML content.
        :param option: Command-line option for save-image-edit.
        :return: None
        """
        edit_cmd = r":%s /" + xml_before_.replace("/", "\/")
        edit_cmd += "/" + xml_after_.replace("/", "\/")
        session = aexpect.ShellSession("sudo -s")

        try:
            LOG.info(f"Executing virsh save-image-edit {vm_save} with option '{option}'...")
            if readonly:
                cmd = f"virsh -r save-image-edit {vm_save} {option}"
            else:
                cmd = f"virsh save-image-edit {vm_save} {option}"
            session.sendline(cmd)
            LOG.info(f"Replacing '{xml_before_}' with '{xml_after_}' in the XML file")
            session.sendline(edit_cmd)
            session.send('\x1b')
            session.send('ZZ')
            session.read_until_any_line_matches(
                patterns=['State file.*%s edited' % vm_save, 'not changed'],
                timeout=5,
                print_func=logging.debug
            )
        except (aexpect.ShellError, aexpect.ExpectError, aexpect.ShellTimeoutError) as details:
            session.close()
            if status_error:
                LOG.info("Failed to do save-image-edit: %s", details)
                if err_msg not in str(details):
                    test.error(f"Save-image-edit failed as expected but did not find the expected error message: '{err_msg}'")
            else:
                test.error(f"Failed to do save-image-edit: {details}")

    def vm_state_check():
        if not libvirt.check_dumpxml(vm, xml_after):
            test.fail("After domain restore the xml is not expected")
        dom_state = virsh.domstate(vm_name, debug=True).stdout.strip()
        LOG.info("VM state after restore is %s", dom_state)
        if virsh_opt == '--running' and dom_state == 'paused':
            test.fail("The domain state is not as expected with option --running")
        elif virsh_opt == '--paused' and dom_state == "running":
            test.fail("The domain state is not as expected with option --paused")

    vm_save = params.get("vm_save", "vm.save")
    pre_state = params.get("pre_state")
    err_msg = params.get('err_msg', '')
    original_xml_dict = eval(params.get('original_xml'))
    updated_xml_dict = eval(params.get('updated_xml'))
    xml_before = f'<on_crash>{original_xml_dict["on_crash"]}</on_crash>'
    xml_after = f'<on_crash>{updated_xml_dict["on_crash"]}</on_crash>'
    readonly = "yes" == params.get("readonly", "no")
    status_error = "yes" == params.get("status_error", "no")
    virsh_opt = params.get("virsh_opt")

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bk_xml = vmxml.copy()
    try:
        # Get a tmp_dir and update the selinux label
        tmp_dir = data_dir.get_tmp_dir()
        if not os.path.dirname(vm_save):
            vm_save = os.path.join(tmp_dir, vm_save)

        LOG.info("Preparing the VM state...")
        new_xml = libvirt_vmxml.set_vm_attrs(vmxml, original_xml_dict)
        LOG.info("Updated XML:\n%s", new_xml)
        if not vm.is_alive():
            vm.start()
        if pre_state == "paused":
            virsh.suspend(vm_name, debug=True)
        vm_state = virsh.domstate(vm_name).stdout_text.strip()
        LOG.info(f"The VM state is '{vm_state}' before save")

        LOG.info("TEST STEP 1: Perform 'virsh save' to save the RAM state of a running domain...")
        cmd_result = virsh.save(vm_name, vm_save, debug=True)
        libvirt.check_result(cmd_result)

        LOG.info("TEST STEP 2: Edit the XML content within the saved state file:")
        edit_image_xml(xml_before, xml_after, virsh_opt)

        if not status_error:
            LOG.info("TEST STEP 3: Restore VM for positive scenarios:")
            cmd_result = virsh.restore(vm_save, debug=True)
            libvirt.check_result(cmd_result)
            LOG.info("TEST STEP 4: Verify VM status and the XML content for positive scenarios:")
            vm_state_check()
    finally:
        # Cleanup
        os.remove(vm_save)
        bk_xml.sync()
        if os.path.exists(vm_save):
            virsh.restore(vm_save)
            os.remove(vm_save)
