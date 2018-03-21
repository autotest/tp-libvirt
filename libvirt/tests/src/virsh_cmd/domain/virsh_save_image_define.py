import os
import logging
import tempfile
import re

from avocado.core import exceptions

from virttest import data_dir
from virttest import virsh
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Test command: virsh save-image-dumpxml <file>
                  virsh save-image-define <file> [--xml <string>]

    1) Prepare test environment.
    2) Execute save-image-dumpxml to get VM xml description
    3) Edit the xml string and call virsh save-image-define to redefine it
    4) Restore VM
    5) Check the new xml of the VM
    """

    def get_image_xml():
        # Invoke save-image-dumpxml
        cmd_result = virsh.save_image_dumpxml(vm_save, debug=True)
        libvirt.check_exit_status(cmd_result)

        xml = cmd_result.stdout.strip()

        match_string = "<name>%s</name>" % vm_name
        if not re.search(match_string, xml):
            raise exceptions.TestFail("The xml from saved state file "
                                      "is invalid")
        return xml

    def redefine_new_xml():
        if restore_state == "running":
            option = "--running"
        elif restore_state == "paused":
            option = "--paused"
        else:
            raise exceptions.TestFail("Unknown save-image-define option")

        cmd_result = virsh.save_image_define(vm_save, xmlfile, option,
                                             debug=True)
        libvirt.check_exit_status(cmd_result)

    def vm_state_check():
        cmd_result = virsh.dumpxml(vm_name, debug=True)
        libvirt.check_exit_status(cmd_result)

        # The xml should contain the match_string
        xml = cmd_result.stdout.strip()
        match_string = "<boot dev='cdrom'/>"
        if not re.search(match_string, xml):
            raise exceptions.TestFail("After domain restore, "
                                      "the xml is not expected")

        domstate = virsh.domstate(vm_name, debug=True).stdout.strip()
        if restore_state != domstate:
            raise exceptions.TestFail("The domain state is not expected")

    # MAIN TEST CODE ###
    # Process cartesian parameters
    vm_name = params.get("main_vm")

    restore_state = params.get("restore_state", "running")
    vm_save = params.get("vm_save", "vm.save")

    try:
        # Get a tmp_dir.
        tmp_dir = data_dir.get_tmp_dir()

        if os.path.dirname(vm_save) is "":
            vm_save = os.path.join(tmp_dir, vm_save)

        # Save the RAM state of a running domain
        cmd_result = virsh.save(vm_name, vm_save, debug=True)
        libvirt.check_exit_status(cmd_result)

        xml = get_image_xml()

        # Replace <boot dev='hd'/> to <boot dev='cdrom'/>
        newxml = xml.replace("<boot dev='hd'/>", "<boot dev='cdrom'/>")
        logging.debug("After string replacement, the new xml is %s", newxml)

        # Write new xml into a tempfile
        tmp_file = tempfile.NamedTemporaryFile(prefix=("new_vm_xml_"),
                                               dir=tmp_dir)
        xmlfile = tmp_file.name
        tmp_file.close()

        with open(xmlfile, 'w') as fd:
            fd.write(newxml)

        # Redefine new xml for domain's saved state file
        redefine_new_xml()

        # Restore domain
        cmd_result = virsh.restore(vm_save, debug=True)
        libvirt.check_exit_status(cmd_result)
        os.remove(vm_save)

        vm_state_check()

    finally:
        # cleanup
        if restore_state == "paused":
            virsh.resume(vm_name)

        if os.path.exists(vm_save):
            virsh.restore(vm_save)
            os.remove(vm_save)

        if os.path.exists(xmlfile):
            os.remove(xmlfile)
