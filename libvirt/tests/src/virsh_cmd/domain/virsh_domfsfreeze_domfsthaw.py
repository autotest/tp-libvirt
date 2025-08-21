import aexpect

from virttest import virsh, utils_disk
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_disk
from virttest.utils_test import libvirt

from provider.virtual_disk import disk_base


def run(test, params, env):
    """
    This test virsh domfsfreeze and domfsthaw commands and their options.

    1) Start a guest with/without guest agent configured;
    2) Freeze the guest file systems with domfsfreeze;
    3) Create a file on guest to see command hang;
    4) Thaw the guest file systems with domfsthaw;
    5) Check the file is already created;
    6) Retouch the file the ensure guest file system are not frozen;
    7) Cleanup test environment.
    """
    def check_freeze(session, mountpoints, file_name):
        """
        Check whether file system has been frozen by touch a test file
        and see if command will hang.
        TODO: A strange part is that when only one mountpoint is frozen, "touch file" in
        other mountpoints will also fail. We should figure out the reason and fix the
        auto script later.

        :param session: Guest session to be tested.
        :param mountpoints: The list of guest mountpoints.
        :param file_name: The name of file to be created on guest.
        """
        test.log.info("TEST_STEP: Check file system freeze.")
        for mntpoint in mountpoints:
            try:
                output = session.cmd_output("touch %s/%s" % (mntpoint, file_name), timeout=10)
                test.fail(
                    "Failed to freeze file system %s. Create file succeeded:\n%s"
                    % (mntpoint, output)
                )
            except aexpect.ShellTimeoutError:
                pass

    def check_thaw(session, mountpoints, file_name):
        """
        Check whether file system has been thawed by check a test file
        prohibited from creation when frozen created and successfully touch
        the file again.

        :param session: Guest session to be tested.
        :param mountpoints: The list of guest mountpoints.
        :param file_name: The name of file to be checked on guest.
        """
        test.log.info("TEST_STEP: Check file system thaw.")
        for mntpoint in mountpoints:
            status, output = session.cmd_status_output("ls %s/%s" % (mntpoint, file_name))
            if status:
                test.fail(
                    "Failed to thaw file system %s. Can't find created file:\n%s"
                    % (mntpoint, output)
                )

            try:
                output = session.cmd_output("touch %s/%s" % (mntpoint, file_name), timeout=10)
            except aexpect.ShellTimeoutError:
                test.fail(
                    "Failed to thaw file system %s. Touch file timeout:\n%s"
                    % (mntpoint, output)
                )

    def attach_second_disk(disk_obj, disk_type):
        """
        Attach second disk to a domain, including:
        1. Create a block type disk
        2. Attach the disk to the domain

        :param disk_obj: The DiskBase object.
        :param disk_type: The type of disk source.
        """
        test.log.info("TEST_STEP: Attach second disk to vm.")
        disk_dict = {
            "type_name": disk_type,
            "target": {"dev": "vdb", "bus": "virtio"},
            "driver": {"name": "qemu", "type": "raw"},
        }

        disk_obj.add_vm_disk(disk_type=disk_type, disk_dict=disk_dict, new_image_path="")

    def mount_second_disk_in_vm(session):
        """
        Mount the second disk in a domain, including:
        1. Make filesystem on the second disk
        2. Mount the second disk in vm

        :param session: The guest console session
        :return: The mountpoint of the second disk
        """
        test.log.info("TEST_STEP: Mount second disk in vm.")
        second_disk = libvirt_disk.get_non_root_disk_name(session)
        return utils_disk.configure_empty_disk(session, second_disk[0], "100M", "linux")[0]

    def generate_random_string(length=10):
        """
        Generate a random string with specified length.
        """
        import random
        import string

        return "".join(
            random.choice(string.ascii_letters + string.digits) for _ in range(length)
        )

    def set_selinux_label(session):
        """
        Per default selinux will block the guest agent from freezing
        the file system when the second disk is attached to the domain.
        This function will set selinux label to allow this.

        :param session: Guest console session
        """
        session.cmd_output(
            "setsebool -P virt_qemu_ga_read_nonsecurity_files on", timeout=120
        )

    def run_agent_command_when_frozen(vm_name, command_name="domtime"):
        """
        Run qemu guest agent command when guest filesystem is fronzen.
        It should fail with reasonable error.

        :param vm_name (string): Vm name
        :param command_name (string): Qemu agent command name, e.g. domtime
        Returns:
            None
        """
        test.log.info("TEST_STEP: Run qemu guest agent command when frozen.")
        fail_patts = [
            r"error: guest agent command failed: unable to execute QEMU agent command '\S+': Command guest-get-time has been disabled: the command is not allowed",
            r"error: internal error: unable to execute QEMU agent command '\S+': Command guest-get-time has been disabled: the command is not allowed",
        ]

        virsh_command = eval("virsh.%s" % command_name)
        res = virsh_command(vm_name)
        libvirt.check_result(res, fail_patts)

    def run_agent_command_when_thawed(vm_name, command_name="domtime"):
        """
        Run qemu guest agent command when guest filesystem is thawed.
        It should succeed.

        :param vm_name (string): Vm name
        :param command_name (string): Qemu agent command name, e.g. domtime
        Returns:
            None
        """
        test.log.info("TEST_STEP: Run qemu guest agent command when thawed.")
        virsh_command = eval("virsh.%s" % command_name)
        res = virsh_command(vm_name)
        libvirt.check_exit_status(res, expect_error=False)

    def cleanup(session, mountpoints, test_file):
        """
        Clean up the test file used for freeze/thaw test.

        :param session: Guest session to be cleaned up.
        :param mountpoints (list): List of mountpoints in guest.
        :param test_file (string): Test file name to be cleaned up.
        """
        for mntpoint in mountpoints:
            status, output = session.cmd_status_output("rm -f %s/%s" % (mntpoint, test_file))
            if status:
                test.error(
                    "Failed to cleanup test file. Can't find created file:\n%s/%s"
                    % (mntpoint, output)
                )

    if not virsh.has_help_command('domfsfreeze'):
        test.cancel("This version of libvirt does not support "
                    "the domfsfreeze/domfsthaw test")

    channel = ("yes" == params.get("prepare_channel", "yes"))
    agent = ("yes" == params.get("start_agent", "yes"))
    specified_mountpoint = params.get("specified_mountpoint", None)
    require_second_disk = "yes" == params.get("require_second_disk", "no")
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    xml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    test_file = generate_random_string() + "_freeze_test"
    try:
        disk_obj = disk_base.DiskBase(test, vm, params)
        if require_second_disk:
            attach_second_disk(disk_obj, disk_type="block")

        # Add or remove qemu-agent from guest before test
        vm.prepare_guest_agent(channel=channel, start=agent)
        session = vm.wait_for_login()

        second_disk_mntpoint = None
        if require_second_disk:
            second_disk_mntpoint = mount_second_disk_in_vm(session)
            set_selinux_label(session)

        freezed_mountpoints = []
        if specified_mountpoint is not None:
            freezed_mountpoints.append(specified_mountpoint)
        else:
            freezed_mountpoints.append("/")
            if second_disk_mntpoint is not None:
                freezed_mountpoints.append(second_disk_mntpoint)

        try:
            # Expected fail message patterns
            fail_patts = []
            if not channel:
                fail_patts.append(r"QEMU guest agent is not configured")
            if not agent:
                # For older version
                fail_patts.append(r"Guest agent not available for now")
                # For newer version
                fail_patts.append(r"Guest agent is not responding")
            # Message patterns test should skip when met
            skip_patts = [
                r'The command \S+ has not been found',
                r'specifying mountpoints is not supported',
            ]

            test.log.info("TEST_STEP: Do domfsfreeze.")
            res = virsh.domfsfreeze(vm_name, mountpoint=specified_mountpoint)
            libvirt.check_result(res, fail_patts, skip_patts)
            if not res.exit_status:
                check_freeze(session, freezed_mountpoints, test_file)
                run_agent_command_when_frozen(vm_name)

            test.log.info("TEST_STEP: Do domfsthaw.")
            res = virsh.domfsthaw(vm_name)
            libvirt.check_result(res, fail_patts, skip_patts)
            if not res.exit_status:
                check_thaw(session, freezed_mountpoints, test_file)
                run_agent_command_when_thawed(vm_name)
        finally:
            cleanup(session, freezed_mountpoints, test_file)
            session.close()
    finally:
        if vm.is_alive():
            vm.destroy()
        if require_second_disk:
            disk_obj.cleanup_disk_preparation(disk_type="block")
        xml_backup.sync()
