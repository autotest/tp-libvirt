import os
import logging

from avocado.utils import process
from avocado.utils import path as utils_path

from virttest import virsh
from virttest import data_dir
from virttest import virt_vm
from virttest import utils_misc
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest import error_context


def run(test, params, env):
    """
    Test command: virsh change-media.

    The command changes the media used by CD or floppy drives.

    Test steps:
    1. Prepare test environment.
    2. Perform virsh change-media operation.
    3. Recover test environment.
    4. Confirm the test result.
    """
    @error_context.context_aware
    def env_pre(old_iso, new_iso):
        """
        Prepare ISO image for test

        :param old_iso: sourse file for insert
        :param new_iso: sourse file for update
        """
        error_context.context("Preparing ISO images")
        process.run("dd if=/dev/urandom of=%s/old bs=1M count=1" % iso_dir, shell=True)
        process.run("dd if=/dev/urandom of=%s/new bs=1M count=1" % iso_dir, shell=True)
        process.run("mkisofs -o %s %s/old" % (old_iso, iso_dir), shell=True)
        process.run("mkisofs -o %s %s/new" % (new_iso, iso_dir), shell=True)

    @error_context.context_aware
    def check_media(session, target_file, action, rw_test=False, options=None):
        """
        Check guest cdrom/floppy files

        :param session: guest session
        :param target_file: the expected files
        :param action: test case action
        :param options: additional options
        """
        if target_device == "hdc" or target_device == "sdc":
            drive_name = session.cmd("cat /proc/sys/dev/cdrom/info | grep -i 'drive name'", ignore_all_errors=True).split()[2]
            if 'block' in options:
                # Check device exists
                if 'sr0' not in drive_name:
                    test.fail("can not find expected drive name when check media")
                else:
                    # For block options, just stop here and skip below checking since the content is empty in block device.
                    return
        if action != "--eject ":
            error_context.context("Checking guest %s files" % target_device)
            if target_device == "hdc" or target_device == "sdc":
                mount_cmd = "mount /dev/%s /media" % drive_name
            else:
                if session.cmd_status("ls /dev/fd0"):
                    session.cmd("mknod /dev/fd0 b 2 0")
                mount_cmd = "mount /dev/fd0 /media"
            session.cmd(mount_cmd)
            if rw_test:
                target_file = "/media/rw_test.txt"
                session.cmd("touch %s" % target_file)
                session.cmd("echo 'Hello World'> %s" % target_file)
                output = session.get_command_output("cat %s" % target_file)
                logging.debug("cat %s output: %s", target_file, output)
            else:
                session.cmd("test -f /media/%s" % target_file)
            session.cmd("umount /media")

        else:
            error_context.context("Ejecting guest cdrom files")
            if target_device == "hdc" or target_device == "sdc":
                if session.cmd_status("mount /dev/%s /media -o loop" % drive_name) == 32:
                    logging.info("Eject succeeded")
            else:
                if session.cmd_status("ls /dev/fd0"):
                    session.cmd("mknod /dev/fd0 b 2 0")
                if session.cmd_status("mount /dev/fd0 /media -o loop") == 32:
                    logging.info("Eject succeeded")

    def add_device(vm_name, init_source="''"):
        """
        Add device for test vm

        :param vm_name: guest name
        :param init_source: source file
        """
        if vm.is_alive():
            virsh.destroy(vm_name)

        device_target_bus = params.get("device_target_bus", "ide")
        readonly = "--mode readonly" if params.get("readonly", True) else ""
        virsh.attach_disk(vm_name, init_source,
                          target_device,
                          "--type %s --sourcetype file --targetbus %s %s --config" % (device_type, device_target_bus, readonly),
                          debug=True)

    def update_device(vm_name, init_iso, options, start_vm):
        """
        Update device iso file for test case

        :param vm_name: guest name
        :param init_iso: source file
        :param options: update-device option
        :param start_vm: guest start flag
        """
        if 'disk_alias' not in locals() or disk_alias is None:
            disk_alias_update = ""
        else:
            disk_alias_update = disk_alias
        device_target_bus = params.get("device_target_bus", "ide")
        readonly = "<readonly/>" if params.get("readonly", True) else ""
        snippet = """
<disk type='file' device='%s'>
<driver name='qemu' type='raw'/>
<source file='%s'/>
<target dev='%s' bus='%s'/>
%s
<alias name='%s'/>
</disk>
""" % (device_type, init_iso, target_device, device_target_bus, readonly, disk_alias_update)
        logging.info("Update xml is %s", snippet)
        with open(update_iso_xml, "w") as update_iso_file:
            update_iso_file.write(snippet)

        cmd_options = "--force "
        if options == "--config" or start_vm == "no":
            cmd_options += " --config"

        # Give domain the ISO image file
        return virsh.update_device(domainarg=vm_name,
                                   filearg=update_iso_xml, flagstr=cmd_options,
                                   debug=True)

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vm_ref = params.get("change_media_vm_ref")
    action = params.get("change_media_action")
    start_vm = params.get("start_vm")
    options = params.get("change_media_options")
    device_type = params.get("change_media_device_type", "cdrom")
    target_device = params.get("change_media_target_device", "hdc")
    source_name = params.get("change_media_source")
    status_error = "yes" == params.get("status_error", "no")
    check_file = params.get("change_media_check_file")
    update_iso_xml_name = params.get("change_media_update_iso_xml")
    init_iso_name = params.get("change_media_init_iso")
    old_iso_name = params.get("change_media_old_iso")
    new_iso_name = params.get("change_media_new_iso")
    source_path = params.get("change_media_source_path", "yes")

    if device_type not in ['cdrom', 'floppy']:
        test.cancel("Got a invalid device type:/n%s" % device_type)

    try:
        utils_path.find_command("mkisofs")
    except utils_path.CmdNotFoundError:
        test.cancel("Command 'mkisofs' is missing. You must "
                    "install it (try 'genisoimage' package.")

    # Check virsh command option
    if options and not status_error:
        libvirt.virsh_cmd_has_option('change-media', options)

    # Backup for recovery.
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    try:
        iso_dir = os.path.join(data_dir.get_tmp_dir(), "tmp")
        old_iso = os.path.join(iso_dir, old_iso_name)
        new_iso = os.path.join(iso_dir, new_iso_name)
        update_iso_xml = os.path.join(iso_dir, update_iso_xml_name)
        if not os.path.exists(iso_dir):
            os.mkdir(iso_dir)
        if not init_iso_name:
            init_iso = ""
        else:
            init_iso = os.path.join(iso_dir, init_iso_name)

        if vm_ref == "name":
            vm_ref = vm_name

        env_pre(old_iso, new_iso)
        # Check domain's disk device
        disk_blk = vm_xml.VMXML.get_disk_blk(vm_name)
        logging.info("disk_blk %s", disk_blk)
        if target_device not in disk_blk:
            logging.info("Adding device")
            add_device(vm_name)

        if vm.is_alive() and start_vm == "no":
            logging.info("Destroying guest...")
            vm.destroy()

        elif vm.is_dead() and start_vm == "yes":
            logging.info("Starting guest...")
            vm.start()

        disk_alias = libvirt.get_disk_alias(vm)
        logging.debug("disk_alias is %s", disk_alias)
        # If test target is floppy, you need to set selinux to Permissive mode.
        result = update_device(vm_name, init_iso, options, start_vm)

        # If the selinux is set to enforcing, if we FAIL, then just SKIP
        force_SKIP = False
        if result.exit_status == 1 and utils_misc.selinux_enforcing() and \
           result.stderr.count("unable to execute QEMU command 'change':"):
            force_SKIP = True

        # Libvirt will ignore --source when action is eject
        if action == "--eject ":
            source = ""
        else:
            source = os.path.join(iso_dir, source_name)
            if source_path == "no":
                source = source_name

        # Setup iscsi target
        if 'block' in options:
            blk_dev = libvirt.setup_or_cleanup_iscsi(is_setup=True,
                                                     is_login=True)
            logging.debug("block dev:%s" % blk_dev)
            source = blk_dev
        # For read&write floppy test, the iso media need a writeable fs
        rw_floppy_test = "yes" == params.get("rw_floppy_test", "no")
        if rw_floppy_test:
            process.run("mkfs.ext3 -F %s" % source, shell=True)

        all_options = action + options + " " + source
        result = virsh.change_media(vm_ref, target_device,
                                    all_options, ignore_status=True, debug=True)
        if status_error:
            if start_vm == "no" and vm.is_dead():
                try:
                    vm.start()
                except virt_vm.VMStartError as detail:
                    result.exit_status = 1
                    result.stderr = str(detail)
            if start_vm == "yes" and vm.is_alive():
                vm.destroy(gracefully=False)
                try:
                    vm.start()
                except virt_vm.VMStartError as detail:
                    result.exit_status = 1
                    result.stderr = str(detail)

        status = result.exit_status

        if not status_error:
            if 'print-xml' in options:
                vmxml_new = vm_xml.VMXML.new_from_dumpxml(vm_name)
                if source in vmxml_new:
                    test.fail("Run command with 'print-xml'"
                              " option, unexpected device was"
                              " found in domain xml")
            else:
                if options == "--config" and vm.is_alive():
                    vm.destroy()
                if vm.is_dead():
                    vm.start()
                if 'block' in options and 'eject' not in action:
                    vmxml_new = vm_xml.VMXML.new_from_dumpxml(vm_name)
                    logging.debug("vmxml: %s", vmxml_new)
                    if not vm_xml.VMXML.check_disk_type(vm_name, source, "block"):
                        test.fail("Disk isn't a 'block' device")
                session = vm.wait_for_login()
                check_media(session, check_file, action, rw_floppy_test, options=options)
                session.close()
        # Negative testing
        if status_error:
            if status:
                logging.info("Expected error (negative testing). Output: %s",
                             result.stderr.strip())
            else:
                test.fail("Unexpected return code %d "
                          "(negative testing)" % status)

        # Positive testing
        else:
            if status:
                if force_SKIP:
                    test.cancel("SELinux is set to enforcing and has "
                                "resulted in this test failing to open "
                                "the iso file for a floppy.")
                test.fail("Unexpected error (positive testing). "
                          "Output: %s" % result.stderr.strip())
            else:
                logging.info("Expected success. Output: %s", result.stdout.strip())
    finally:
        # Clean the iso dir  and clean the device
        update_device(vm_name, "", options, start_vm)
        if 'block' in options:
            libvirt.setup_or_cleanup_iscsi(is_setup=False)
        vm.destroy(gracefully=False)
        # Recover xml of vm.
        vmxml_backup.sync()
        utils_misc.safe_rmdir(iso_dir)
