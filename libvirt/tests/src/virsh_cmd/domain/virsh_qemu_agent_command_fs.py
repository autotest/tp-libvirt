import time
import logging
from autotest.client.shared import error
from virttest import virsh
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
    tmp_file = params.get("tmp_file", "/tmp/test.file")
    xml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    vm_xml.VMXML.set_agent_channel(vm_name)
    vm.start()
    session = vm.wait_for_login()
    session.cmd("qemu-ga -d")
    stat_ps = session.cmd_status("ps aux |grep [q]emu-ga")
    if stat_ps:
        session.close()
        xml_backup.sync()
        raise error.TestError("Fail to start qemu-guest-agent!")

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
            except (IndexError, ValueError), details:
                raise error.TestFail("Get dirty info failed: %s" % details)

        # Do operation before freeze guest filesystem
        session.cmd("rm -f %s" % tmp_file)
        session.cmd_output("cp /dev/zero %s 2>/dev/null &" % tmp_file)
        time.sleep(5)
        # Get original dirty data
        org_dirty_info = get_dirty(session)
        fz_cmd_result = virsh.qemu_agent_command(vm_name, freeze_cmd,
                                                 ignore_status=True,
                                                 debug=True)
        libvirt.check_exit_status(fz_cmd_result)
        # Wait for freeze filesystem
        time.sleep(1)

        # Get frozen dirty data
        fz_dirty_info = get_dirty(session, True)
        st_cmd_result = virsh.qemu_agent_command(vm_name, status_cmd,
                                                 ignore_status=True,
                                                 debug=True)
        libvirt.check_exit_status(st_cmd_result)
        if not st_cmd_result.stdout.count("frozen"):
            raise error.TestFail("Guest filesystem status is not frozen: %s"
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
        if not st_cmd_result.stdout.count("thawed"):
            raise error.TestFail("Guest filesystem status is not thawed: %s"
                                 % st_cmd_result.stdout.strip())
        logging.info("Original dirty data: %s" % org_dirty_info)
        logging.info("Frozen dirty data: %s" % fz_dirty_info)
        logging.info("Thawed dirty data: %s" % tw_dirty_info)
        if not tw_dirty_info or not org_dirty_info:
            raise error.TestFail("The thawed dirty data should not be 0!")
        if fz_dirty_info:
            raise error.TestFail("The frozen dirty data should be 0!")
    finally:
        # Thaw the file system that remove action can be done
        virsh.qemu_agent_command(vm_name, thaw_cmd, ignore_status=True)
        session.cmd("rm -f %s" % tmp_file)
        session.close()
        xml_backup.sync()
