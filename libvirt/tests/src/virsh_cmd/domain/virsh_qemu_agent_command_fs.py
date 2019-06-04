import os
import time
import logging

from virttest import virsh
from virttest import data_dir
from virttest import utils_disk
from virttest import utils_misc
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml


def run(test, params, env):
    """
    Test command: virsh qemu-agent-command.
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    status_cmd = params.get("status_cmd", "")
    freeze_cmd = params.get("freeze_cmd", "")
    thaw_cmd = params.get("thaw_cmd", "")
    xml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    try:
        def get_dirty(session, frozen=False):
            """
            Get dirty data of guest
            """
            try:
                data_cmd = "cat /proc/meminfo | grep Dirty"
                if not frozen:
                    result = utils_misc.wait_for(lambda:
                                                 int(session.
                                                     cmd_output(data_cmd).
                                                     strip().split()[1]) != 0,
                                                 60)
                    if result:
                        return int(session.cmd_output(data_cmd).strip().
                                   split()[1])
                    else:
                        return 0
                    dirty_info = session.cmd_output(data_cmd).strip()
                    return int(dirty_info.split()[1])
                else:
                    result = utils_misc.wait_for(lambda:
                                                 int(session.
                                                     cmd_output(data_cmd).
                                                     strip().split()[1]) == 0,
                                                 60)
                    if result:
                        return 0
                    else:
                        return int(session.cmd_output(data_cmd).strip().
                                   split()[1])
            except (IndexError, ValueError) as details:
                test.fail("Get dirty info failed: %s" % details)

        device_source_path = os.path.join(data_dir.get_tmp_dir(), "disk.img")
        device_source = libvirt.create_local_disk("file", path=device_source_path,
                                                  disk_format="qcow2")
        vm.prepare_guest_agent()

        # Do operation before freeze guest filesystem
        session = vm.wait_for_login()
        tmp_file = "/mnt/test.file"
        try:
            # Create extra image and attach to guest, then mount
            old_parts = utils_disk.get_parts_list(session)
            ret = virsh.attach_disk(vm_name, device_source, "vdd")
            if ret.exit_status:
                test.fail("Attaching device failed before testing agent:%s" % ret.stdout.strip())
            time.sleep(1)
            new_parts = utils_disk.get_parts_list(session)
            added_part = list(set(new_parts).difference(set(old_parts)))
            session.cmd("mkfs.ext3 -F /dev/{0} && mount /dev/{0} /mnt".format(added_part[0]))

            # Generate dirty memory
            session.cmd("rm -f %s" % tmp_file)
            session.cmd_output("cp /dev/zero %s 2>/dev/null &" % tmp_file)
            # Get original dirty data
            org_dirty_info = get_dirty(session)
            fz_cmd_result = virsh.qemu_agent_command(vm_name, freeze_cmd,
                                                     ignore_status=True,
                                                     debug=True)
            libvirt.check_exit_status(fz_cmd_result)

            # Get frozen dirty data
            fz_dirty_info = get_dirty(session, True)
            st_cmd_result = virsh.qemu_agent_command(vm_name, status_cmd,
                                                     ignore_status=True,
                                                     debug=True)
            libvirt.check_exit_status(st_cmd_result)
            if not st_cmd_result.stdout.strip().count("frozen"):
                test.fail("Guest filesystem status is not frozen: %s"
                          % st_cmd_result.stdout.strip())

            tw_cmd_result = virsh.qemu_agent_command(vm_name, thaw_cmd,
                                                     ignore_status=True,
                                                     debug=True)
            libvirt.check_exit_status(tw_cmd_result)

            # Get thawed dirty data
            tw_dirty_info = get_dirty(session)
            st_cmd_result = virsh.qemu_agent_command(vm_name, status_cmd,
                                                     ignore_status=True,
                                                     debug=True)
            libvirt.check_exit_status(st_cmd_result)
            if not st_cmd_result.stdout.strip().count("thawed"):
                test.fail("Guest filesystem status is not thawed: %s"
                          % st_cmd_result.stdout.strip())
            logging.info("Original dirty data: %s" % org_dirty_info)
            logging.info("Frozen dirty data: %s" % fz_dirty_info)
            logging.info("Thawed dirty data: %s" % tw_dirty_info)
            if not tw_dirty_info or not org_dirty_info:
                test.fail("The thawed dirty data should not be 0!")
            if fz_dirty_info:
                test.fail("The frozen dirty data should be 0!")
        finally:
            # Thaw the file system that remove action can be done
            virsh.qemu_agent_command(vm_name, thaw_cmd, ignore_status=True)
            session.cmd("rm -f %s" % tmp_file)
            session.close()
    finally:
        xml_backup.sync()
        os.remove(device_source_path)
