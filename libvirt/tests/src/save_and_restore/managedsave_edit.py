import logging
import os
import re
import shutil

from virttest import virsh
from virttest.utils_test import libvirt

LOG = logging.getLogger("avocado.test." + __name__)


def vm_state_check(test, vm_name, new_disk_path, virsh_opt):
    """
    Check domain xml and state after restore from saved image.
    """
    cmd_re = virsh.dumpxml(vm_name, debug=True)
    if cmd_re.exit_status:
        test.fail(f"Failed to dump xml of domain '{vm_name}'")

    # The xml should contain the match_string
    xml = cmd_re.stdout.strip()
    xml_pattern = f"<source file='{new_disk_path}'"
    LOG.info(f"Checking for disk path '{new_disk_path}' in domain XML.")

    if not re.search(xml_pattern, xml):
        test.fail(
            f"After domain restore the xml is not expected, could not find source file='{new_disk_path}'"
        )
    else:
        LOG.info(f"Found source file='{new_disk_path}' in the domain xml successfully!")

    dom_state = virsh.domstate(vm_name, debug=True).stdout.strip()
    LOG.info(f"VM state after restore is '{dom_state}'")
    if virsh_opt == "--running" and dom_state != "running":
        test.fail(
            "The domain state is not as expected with option '--running'. Got 'paused', expected 'running'"
        )
    elif virsh_opt == "--paused" and dom_state != "paused":
        test.fail(
            "The domain state is not as expected with option '--paused'. Got 'running', expected 'paused'"
        )


def run(test, params, env):
    """
    Test command: virsh managedsave-edit <vm_name> [option]
    1) Prepare test environment, ensure VM is in requested state: running or paused
    2) Do managedsave for the VM
    3) Execute virsh managedsave-edit to edit xml
    4) Start VM
    5) Check the new xml of the VM and its state
    """
    pre_state = params.get("pre_state")
    readonly = "yes" == params.get("readonly", "no")
    status_error = "yes" == params.get("status_error", "no")
    virsh_opt = params.get("virsh_opt")

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    new_path = None
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
        disk_path = vm.get_first_disk_devices()["source"]
        new_path = f"{disk_path}.test"

        # Ensure the new path does not already exist
        if os.path.exists(new_path):
            LOG.warning(f"The path '{new_path}' already exists. Removing it...")
            os.remove(new_path)

        shutil.copy(disk_path, new_path)
        LOG.info(f"The original disk path is '{disk_path}'")
        LOG.info(f"The new disk path will be: '{new_path}'")

        replace_string = r":%s /" + disk_path.replace("/", "\/")
        replace_string += "/" + new_path.replace("/", "\/")
        stat = libvirt.exec_virsh_edit(
            vm_name,
            [replace_string],
            managedsave_edit=True,
            readonly=readonly,
            virsh_opt=virsh_opt,
        )
        if not stat:
            if status_error:
                LOG.info("Failed to edit the xml, this was expected!")
            else:
                test.fail("managedsave-edit failed!")
        else:
            if status_error:
                test.fail("managedsave-edit succeeded when it should have failed!")

        if not status_error:
            LOG.info("TEST STEP 3: Start VM for positive scenarios:")
            cmd_result = virsh.start(vm_name, debug=True)
            libvirt.check_result(cmd_result)
            LOG.info(
                "TEST STEP 4: Verify VM status and the XML content for positive scenarios:"
            )
            vm_state_check(test, vm_name, new_path, virsh_opt)
    except Exception as e:
        test.error(f"Unexpected error happened during the test execution: {e}")
    finally:
        # Cleanup
        if new_path and os.path.exists(new_path):
            LOG.info(f"Remove the file {new_path}")
            os.remove(new_path)
        virsh.managedsave_remove(vm_name, debug=True)
