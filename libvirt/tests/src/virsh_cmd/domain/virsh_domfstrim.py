import os
import logging
import time

from avocado.utils import path as utils_path
from avocado.utils import linux_modules
from avocado.utils import process

from virttest import virsh
from virttest import utils_misc
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.disk import Disk
from virttest.libvirt_xml.devices.controller import Controller

from provider import libvirt_version


def run(test, params, env):
    """
    Test domfstrim command, make sure that all supported options work well

    Test scenaries:
    1. fstrim without options
    2. fstrim with --minimum with large options
    3. fstrim with --minimum with small options

    Note: --mountpoint still not supported so will not test here
    """
    def recompose_xml(vm_name, scsi_disk):
        """
        Add scsi disk, guest agent and scsi controller for guest
        :param: vm_name: Name of domain
        :param: scsi_disk: scsi_debug disk name
        """

        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        disk_path = scsi_disk
        # Add scsi disk xml
        scsi_disk = Disk(type_name="block")
        scsi_disk.device = "lun"
        scsi_disk.source = scsi_disk.new_disk_source(
            **{'attrs': {'dev': disk_path}})
        scsi_disk.target = {'dev': "sdb", 'bus': "scsi"}
        find_scsi = "no"
        controllers = vmxml.xmltreefile.findall("devices/controller")
        for controller in controllers:
            if controller.get("type") == "scsi":
                find_scsi = "yes"
        vmxml.add_device(scsi_disk)

        # Add scsi disk controller
        if find_scsi == "no":
            scsi_controller = Controller("controller")
            scsi_controller.type = "scsi"
            scsi_controller.index = "0"
            scsi_controller.model = "virtio-scsi"
            vmxml.add_device(scsi_controller)

        # Redefine guest
        vmxml.sync()

    if not virsh.has_help_command('domfstrim'):
        test.cancel("This version of libvirt does not support "
                    "the domfstrim test")

    try:
        utils_path.find_command("lsscsi")
    except utils_path.CmdNotFoundError:
        test.cancel("Command 'lsscsi' is missing. You must "
                    "install it.")

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    status_error = ("yes" == params.get("status_error", "no"))
    minimum = params.get("domfstrim_minimum")
    mountpoint = params.get("domfstrim_mountpoint")
    options = params.get("domfstrim_options", "")
    is_fulltrim = ("yes" == params.get("is_fulltrim", "yes"))
    uri = params.get("virsh_uri")
    unprivileged_user = params.get('unprivileged_user')
    has_qemu_ga = not ("yes" == params.get("no_qemu_ga", "no"))
    start_qemu_ga = not ("yes" == params.get("no_start_qemu_ga", "no"))
    if unprivileged_user:
        if unprivileged_user.count('EXAMPLE'):
            unprivileged_user = 'testacl'

    if not libvirt_version.version_compare(1, 1, 1):
        if params.get('setup_libvirt_polkit') == 'yes':
            test.cancel("API acl test not supported in current"
                        " libvirt version.")

    # Do backup for origin xml
    xml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    try:
        vm = env.get_vm(vm_name)
        if not vm.is_alive():
            vm.start()
        session = vm.wait_for_login()
        bef_list = session.cmd_output("fdisk -l|grep ^/dev|"
                                      "cut -d' ' -f1").split("\n")
        session.close()
        vm.destroy()

        # Load module and get scsi disk name
        linux_modules.load_module("scsi_debug lbpu=1 lbpws=1")
        time.sleep(5)
        scsi_disk = process.run("lsscsi|grep scsi_debug|"
                                "awk '{print $6}'", shell=True).stdout_text.strip()
        # Create partition
        with open("/tmp/fdisk-cmd", "w") as cmd_file:
            cmd_file.write("n\np\n\n\n\nw\n")

        output = process.run("fdisk %s < /tmp/fdisk-cmd"
                             % scsi_disk, shell=True).stdout_text.strip()
        logging.debug("fdisk output %s", output)
        os.remove("/tmp/fdisk-cmd")
        # Format disk
        output = process.run("mkfs.ext3 %s1" % scsi_disk, shell=True).stdout_text.strip()
        logging.debug("output %s", output)
        # Add scsi disk in guest
        recompose_xml(vm_name, scsi_disk)

        # Prepare guest agent and start guest
        if has_qemu_ga:
            vm.prepare_guest_agent(start=start_qemu_ga)
        else:
            # Remove qemu-ga channel
            vm.prepare_guest_agent(channel=has_qemu_ga, start=False)

        guest_session = vm.wait_for_login()
        # Get new generated disk
        af_list = guest_session.cmd_output("fdisk -l|grep ^/dev|"
                                           "cut -d' ' -f1").split('\n')
        new_disk = "".join(list(set(bef_list) ^ set(af_list)))
        # Mount disk in guest
        guest_session.cmd("mkdir -p /home/test && mount %s /home/test" %
                          new_disk)

        # Do first fstrim before all to get original map for compare
        cmd_result = virsh.domfstrim(vm_name)
        if cmd_result.exit_status != 0:
            if not status_error:
                test.fail("Fail to do virsh domfstrim, error %s" %
                          cmd_result.stderr)

        def get_diskmap_size():
            """
            Collect size from disk map
            :return: disk size
            """
            map_cmd = "cat /sys/bus/pseudo/drivers/scsi_debug/map"
            diskmap = process.run(map_cmd, shell=True).stdout_text.strip('\n\x00')
            sum = 0
            for i in diskmap.split(","):
                sum = sum + int(i.split("-")[1]) - int(i.split("-")[0])
            logging.debug("disk map (size:%d) is %s", sum, diskmap)
            return sum

        ori_size = get_diskmap_size()

        # Write date in disk
        dd_cmd = "dd if=/dev/zero of=/home/test/file bs=1048576 count=5; sync"
        guest_session.cmd(dd_cmd)

        def _full_mapped():
            """
            Do full map check
            :return: True or False
            """
            full_size = get_diskmap_size()
            return (ori_size < full_size)

        if not utils_misc.wait_for(_full_mapped, timeout=30):
            test.error("Scsi map is not updated after dd command.")

        full_size = get_diskmap_size()

        # Remove disk content in guest
        guest_session.cmd("rm -rf /home/test/*; sync")
        guest_session.close()

        def _trim_completed():
            """
            Do empty fstrim check
            :return: True of False
            """
            cmd_result = virsh.domfstrim(vm_name, minimum, mountpoint, options,
                                         unprivileged_user=unprivileged_user,
                                         uri=uri)
            if cmd_result.exit_status != 0:
                if not status_error:
                    test.fail("Fail to do virsh domfstrim, error %s"
                              % cmd_result.stderr)
                else:
                    logging.info("Fail to do virsh domfstrim as expected: %s",
                                 cmd_result.stderr)
                    return True

            empty_size = get_diskmap_size()
            logging.info("Trimmed disk to %d", empty_size)

            if is_fulltrim:
                return empty_size <= ori_size
            else:
                # For partly trim will check later
                return False

        if not utils_misc.wait_for(_trim_completed, timeout=30):
            # Get result again to check partly fstrim
            empty_size = get_diskmap_size()
            if not is_fulltrim:
                if ori_size < empty_size <= full_size:
                    logging.info("Success to do fstrim partly")
                    return True
            test.fail("Fail to do fstrim. (original size: %s), "
                      "(current size: %s), (full size: %s)" %
                      (ori_size, empty_size, full_size))
        logging.info("Success to do fstrim")

    finally:
        # Do domain recovery
        vm.shutdown()
        xml_backup.sync()

        def _unload():
            linux_modules.unload_module("scsi_debug")
            return True
        utils_misc.wait_for(_unload, timeout=30, ignore_errors=True)
