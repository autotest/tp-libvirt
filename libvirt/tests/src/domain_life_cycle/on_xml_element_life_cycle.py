#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Red Hat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Walter Herold Veedla <wveedla@redhat.com>
#

import logging
import pathlib
import tempfile

from aexpect import remote
from virttest import virsh
from virttest import virt_vm
from virttest import utils_misc
from virttest.libvirt_xml import vm_xml


LOG = logging.getLogger('avocado.' + __name__)


def backup_nvram_file(source_path, destination_path) -> bool:
    """
    Backup a file from source_path to destination_path.

    :param source_path: Path to the source file.
    :param destination_path: Path to the destination backup file.

    :return: True if the backup is successful, False otherwise.
    """
    source_path = pathlib.Path(source_path)
    destination_path = pathlib.Path(destination_path)

    try:
        LOG.debug("Backing up file from source path: %s to %s", str(source_path), str(destination_path))
        destination_path.write_bytes(source_path.read_bytes())
        LOG.debug("Backup successful: %s -> %s" % (source_path, destination_path))
        return True
    except Exception as e:
        LOG.error("Backup failed: %s" % e)
        return False


def restore_nvram_file(backup_path, destination_path) -> bool:
    """
    Restore a file from backup_path to destination_path.

    :param backup_path: Path to the backup file.
    :param destination_path: Path to the destination file.

    :return: True if the restore is successful, False otherwise.
    """
    backup_path = pathlib.Path(backup_path)
    destination_path = pathlib.Path(destination_path)

    try:
        destination_path.write_bytes(backup_path.read_bytes())
        LOG.debug("Restore successful: %s -> %s" % (backup_path, destination_path))
        return True
    except Exception as e:
        LOG.error("Restore failed: %s" % e)
        return False


def run(test, params, env):
    """
    VM Lifecycle Management Test:

    This test performs a series of steps to validate that the behaviour described by xml attributes
    on_poweroff, on_reboot and on_crash are properly observed by the virtual machine.

    Test Steps:
    0. Prepare test environment
    1. Check the guest can gracefully shutdown via 'virsh.shutdown()' command.
    2. Reboot the guest inside the guest OS and verify it's properly shut down.
    3. Perform a 'virsh reboot <guest>' and confirm the VM is shut down afterward.
    4. Execute a 'virsh shutdown <guest>' and ensure the VM is shut down afterward.
    5. Restore environment

    :param test: QEMU test object.
    :param params: Dictionary with the test parameters.
    :param env: Dictionary with test environment.
    """
    LOGIN_WAIT_TIMEOUT = float(params.get("login_timeout"))
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()
    session = None

    IS_UEFI = None
    temp_dir = None
    source_file = None
    backup_file = None

    try:
        # Test step 1: Check the guest can shutdown via virsh.shutdown()
        LOG.info("Starting test step 1.")
        if not vm.is_alive():
            vm.start()
        session = vm.wait_for_login(timeout=LOGIN_WAIT_TIMEOUT)
        IS_UEFI = utils_misc.is_linux_uefi_guest(lambda x: utils_misc.cmd_status_output(x, session=session)) or \
            utils_misc.is_windows_uefi_guest(lambda x: utils_misc.cmd_status_output(x, session=session))
        LOG.debug("VM is UEFI: %s", IS_UEFI)
        if not vm.shutdown():
            test.error("VM failed initial shutdown")
        LOG.info("Test step 1 passed.")
    finally:
        if session:
            session.close()

    if IS_UEFI:
        # Backup the efi vars file: (UEFI special edge case)
        # To update a vm's xml we use vmxml.sync()
        # sync() uses virsh.undefine and virsh.define in the backend
        # virsh.undefine deletes the efi vars nvram file
        # virsh.define does not "restore" it
        # after starting the UEFI vm it sees the file is not populated and issues a reboot
        # reboot=destroy, vm does not come up
        # try to start the vm, goto step5
        # to work around this we back up the file before sync and restore it after sync
        source_file = vmxml.get_os().get_nvram()
        temp_dir = tempfile.TemporaryDirectory()
        backup_file = pathlib.Path(temp_dir.name) / pathlib.Path(source_file).name

        backup_success = backup_nvram_file(source_file, backup_file)
        if not backup_success:
            test.error("Backing up nvram file failed")

    vmxml.set_on_poweroff(params.get("on_poweroff"))
    LOG.debug("Set on_poweroff to: %s" % vmxml.get_on_poweroff())
    vmxml.set_on_reboot(params.get("on_reboot"))
    LOG.debug("Set on_reboot to: %s" % vmxml.get_on_reboot())
    vmxml.set_on_crash(params.get("on_crash"))
    LOG.debug("Set on_crash to: %s" % vmxml.get_on_crash())
    vmxml.sync()

    if IS_UEFI:
        restore_success = restore_nvram_file(backup_file, source_file)
        if not restore_success:
            test.error("Restoring nvram file failed")

    if vm.is_alive():
        test.error("VM failed initial shutdown")
    try:
        # Test step 2: "reboot" guest inside the guest OS
        LOG.info("Starting test step 2.")
        if not vm.is_alive():
            LOG.debug("Step 2 starting vm")
            vm.start()
        session = vm.wait_for_login(timeout=LOGIN_WAIT_TIMEOUT)
        try:
            vm.reboot(method="shell", timeout=10)  # timeout = 10 because we don't want to login but wwait for the vm to go down
        except (remote.LoginTimeoutError, virt_vm.VMDeadError):
            # We are expecting this error and **must** ignore it otherwise it will halt the execution
            LOG.debug(f'Step 2: Exception caught as expected')
            pass

        if not utils_misc.wait_for(
            lambda: not session.is_responsive(),
                timeout=LOGIN_WAIT_TIMEOUT):
            test.fail("Step 2: VM is still alive after console reboot with behaviour set to 'destroy'")
        if vm.is_alive():
            test.fail("VM is still alive after console reboot")
        LOG.info("Test step 2 passed.")

        # Test step 3: "virsh reboot <guest>"
        LOG.info("Starting test step 3.")
        vm.start()
        session = vm.wait_for_login(timeout=LOGIN_WAIT_TIMEOUT)
        if not virsh.reboot(vm_name):
            test.error("Reboot failed")
        if not utils_misc.wait_for(
            lambda: not session.is_responsive(),
                timeout=LOGIN_WAIT_TIMEOUT):
            test.fail("Step 3: VM is still alive after reboot with behaviour set to 'destroy'")
        LOG.info("Test step 3 passed.")

        # Test step 4: "virsh shutdown <guest>"
        LOG.info("Starting test step 4.")
        vm.start()
        session = vm.wait_for_login(timeout=LOGIN_WAIT_TIMEOUT)
        if not virsh.shutdown(vm_name):
            test.reboot("Shutdown failed")
        if not utils_misc.wait_for(
            lambda: not session.is_responsive(),
                timeout=LOGIN_WAIT_TIMEOUT):
            test.fail("Step 4: VM is still alive after shutdown with behaviour set to 'destroy'")
        LOG.info("Test step 4 passed.")

    finally:
        if session:
            session.close()
        backup_xml.sync()
        if IS_UEFI:
            temp_dir.cleanup()
