import re
import logging
import os
import time
import locale

from avocado.utils import process

from virttest import virsh
from virttest import data_dir
from virttest import utils_disk
from virttest.libvirt_xml import vm_xml
from virttest.staging import lv_utils
from virttest.utils_test import libvirt


def reset_domain(vm, **kwargs):
    """
    Setup guest agent in domain

    :param vm: The vm object
    :param kwargs: The key words to reset guest
    """
    start_vm = kwargs.get("start_vm", True)
    start_ga = kwargs.get("start_ga", True)
    prepare_channel = kwargs.get("prepare_channel", True)
    if not start_vm:
        return
    if vm.is_alive():
        vm.destroy()
    if start_vm:
        vm.start()
    if start_ga and prepare_channel:
        vm.prepare_guest_agent(start=True, channel=True)
    elif not prepare_channel:
        # Guest agent unable to be started without channel
        vm.prepare_guest_agent(start=False, channel=False)
    elif not start_ga:
        vm.prepare_guest_agent(start=False)


def convert_to_mpath_device(session, lvname):
    """
    Return the mapped multipath device by logical volume name

    :param session: Guest session
    :param lvname: Logical Volume Name
    :return: The mapped multipathed_device or None
    """
    names_map = lv_utils.gen_lvmap_mpath(session)
    if names_map and names_map.get(lvname):
        return names_map.get(lvname)
    else:
        return None


def get_mount_fs(session):
    """
    Return a dict's list include guest mounted filesystems
    with the mount Command.

    :param session: Guest session
    :return: A dict's list with the format align to virsh.domfsinfo
             [{"Mountpoint": value, "Name": value,
               "Type": value, "Target": value}]
    """
    list_fs = None
    if session is not None:
        cmd = 'mount | grep ^/dev'
        mnt_info = session.cmd_output(cmd).strip()
        lines = mnt_info.splitlines()
        if len(lines) >= 1:
            list_fs = []
            for line in lines:
                values = line.split()
                fs_name, mount_point, fs_type = values[0], values[2], values[4]
                if fs_name.count("/dev/mapper/"):
                    # Process the device mapper naming
                    lv_path = fs_name.split("/dev/mapper/")[1].split("-")
                    vg_name, lv_name = lv_path[0], lv_path[1]
                    fs_name = convert_to_mpath_device(session, lv_name)
                    target = lv_utils.get_vg_mapped_blk_target(vg_name, session)
                else:
                    fs_name = fs_name.split("/dev/")[1]
                    target = re.findall(r'[a-z]+', fs_name)[0]
                dict_fs = {"Mountpoint": mount_point, "Name": fs_name,
                           "Type": fs_type, "Target": target}
                list_fs.append(dict_fs)
    return list_fs


def check_domfsinfo(domfsinfo, expected_results, test):
    """
    Check the consistency between domfsinfo command output
    and expected results.

    :param domfsinfo: A dict's list constructed from
                      virsh.domfsinfo command in the form like:
                      [{"Mountpoint": value, "Name": value,
                        "Type": value, "Target": value}]
    :param expected_results: A dict's list constructed from mount command
                             in the same format with domfsinfo
    :param test: The test object
    :return: True or raise exceptions
    """
    expected_fs_count = len(expected_results)
    real_fs_count = len(domfsinfo)
    encoding = locale.getpreferredencoding()
    if real_fs_count != expected_fs_count:
        test.fail("Expected number of mounted filesystems is %s, "
                  "but got %s from virsh command" %
                  (expected_fs_count, real_fs_count))
    for real_fs in domfsinfo:
        fs_name = real_fs.get("Name")
        for expect_fs in expected_results:
            if fs_name == expect_fs.get("Name"):
                for key, value in expect_fs.items():
                    if real_fs[key] != value.encode(encoding).decode(encoding):
                        test.fail("Expect filesystem %s has attribute %s=%s, "
                                  "but got %s=%s from virsh command" %
                                  (fs_name, key, value, key, real_fs[key]))
    logging.debug("The filesystems from domfsinfo consistency as expected")


def check_output(output, pattern, test, expected=True):
    """
    Function to compare the output and the pattern

    :param output: String, command stdout
    :param pattern: String to match against the output
    :param test: Avocado test object
    :param expected: Expect matched or not
    :return: Logging or raise exception
    """
    if expected:
        if re.search(pattern, output):
            logging.debug("Succeed to match pattern %s as expected", pattern)
        else:
            test.fail("Expect matched pattern %s, but got from virsh.domfsinfo:\n%s"
                      % (pattern, output))
    else:
        if re.search(pattern, output):
            test.fail("Expect not matched pattern %s, but got from virsh.domfsinfo:\n%s"
                      % (pattern, output))
        else:
            logging.debug("Pattern %s not matched as expected", pattern)


def run(test, params, env):
    """
    Test command: domfsinfo [--domain]

    The command gets information of domain's mounted filesystems.
    """
    start_vm = ("yes" == params.get("start_vm", "yes"))
    start_ga = ("yes" == params.get("start_ga", "yes"))
    prepare_channel = ("yes" == params.get("prepare_channel", "yes"))
    status_error = ("yes" == params.get("status_error", "no"))
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    mount_dir = params.get("mount_dir", None)
    quiet_mode = ("yes" == params.get("quiet_mode", False))
    readonly_mode = ("yes" == params.get("readonly_mode", False))
    nfs_mount = ("yes" == params.get("nfs_mount", False))
    domfsfreeze = ("yes" == params.get("domfsfreeze", False))

    # Hotplug and Unplug options
    hotplug_unplug = ("yes" == params.get("hotplug_unplug", False))
    disk_name = params.get("disk_name", "test")
    disk_path = os.path.join(data_dir.get_tmp_dir(), disk_name)
    disk_target = params.get("disk_target", "vdb")
    fs_type = params.get("fs_type", "ext3")
    new_part = ""

    fail_pat = []
    check_point_msg = params.get("check_point_msg", "")
    if check_point_msg:
        for msg in check_point_msg.split(";"):
            fail_pat.append(msg)

    def hotplug_domain_disk(domain, target, source=None, hotplug=True):
        """
        Hot-plug/Hot-unplug disk for domain

        :param domain: Guest name
        :param source: Source of disk device, can leave None if hotplug=False
        :param target: Target of disk device
        :param hotplug: True means hotplug, False means hot-unplug
        :return: Virsh command object
        """
        if hotplug:
            result = virsh.attach_disk(domain, source, target, "--live",
                                       ignore_status=False, debug=True)
        else:
            session = vm.wait_for_login()
            try:
                session.cmd("umount %s" % mount_dir)
                session.close()
            except:
                test.error("fail to unmount the disk before unpluging the disk")
            result = virsh.detach_disk(domain, target, "--live",
                                       ignore_status=False, debug=True)
        # It need more time for attachment to take effect
        time.sleep(5)

    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    cleanup_nfs = False
    try:
        reset_kwargs = {"start_vm": start_vm,
                        "start_ga": start_ga,
                        "prepare_channel": prepare_channel}
        reset_domain(vm, **reset_kwargs)

        if domfsfreeze:
            result = virsh.domfsfreeze(vm_name, debug=True)
            if result.exit_status:
                test.fail("Failed to execute virsh.domfsfreeze:\n%s" %
                          result.stderr)
        if nfs_mount:
            nfs_device = libvirt.setup_or_cleanup_nfs(True, mount_dir=mount_dir,
                                                      is_mount=True)
            if nfs_device:
                cleanup_nfs = True
        if hotplug_unplug:
            session = vm.wait_for_login()
            new_device = libvirt.create_local_disk("file", path=disk_path, size="1")
            parts_list_before_attach = utils_disk.get_parts_list(session)
            hotplug_domain_disk(vm_name, disk_target, new_device)
            parts_list_after_attach = utils_disk.get_parts_list(session)
            new_part = list(set(parts_list_after_attach).difference(set(parts_list_before_attach)))[0]
            logging.debug("The new partition is %s", new_part)
            libvirt.mkfs("/dev/%s" % new_part, fs_type, session=session)
            session.cmd_status("mkdir -p {0} ; mount /dev/{1} {0}; ls {0}".format(mount_dir, new_part))
            session.close()

        # Run test case
        command_dargs = {"readonly": readonly_mode, "quiet": quiet_mode,
                         "debug": True}
        result = virsh.domfsinfo(vm_name, **command_dargs)
        if not result.exit_status:
            if fail_pat:
                test.fail("Expected fail with %s, but run succeed:\n%s" %
                          (fail_pat, result))
        else:
            if not fail_pat:
                test.fail("Expected success, but run failed:\n%s" % result)
            else:
                # If not any pattern matches(fail_pat, result.stderr)
                if not any(p in result.stderr for p in fail_pat):
                    test.fail("Expected fail with one of %s, but failed with:\n%s" %
                              (fail_pat, result))
        # Check virsh.domfsinfo output
        cmd_output = result.stdout.strip()
        if quiet_mode:
            head_pat = "Mountpoint\s+Name\s+Type\s+Target"
            check_output(cmd_output, head_pat, test, expected=False)
        elif nfs_mount:
            check_output(cmd_output, mount_dir, test, expected=False)
        elif hotplug_unplug:
            blk_target = re.findall(r'[a-z]+', new_part)[0]
            disk_pat = "%s\s+%s\s+%s\s+%s" % (mount_dir, new_part, fs_type, blk_target)
            check_output(cmd_output, disk_pat, test, expected=True)
            # Unplug domain disk
            hotplug_domain_disk(vm_name, target=new_part, hotplug=False)
            result = virsh.domfsinfo(vm_name, **command_dargs)
            if result.exit_status:
                test.fail("Failed to run virsh.domfsinfo after disk unplug:\n%s"
                          % result.stderr)
            check_output(result.stdout.strip(), disk_pat, test, expected=False)
        else:
            # Verify virsh.domfsinfo consistency
            if not status_error:
                session = vm.wait_for_login(timeout=120)
                domfsinfo = vm.domfsinfo()
                expected_result = get_mount_fs(session)
                if domfsinfo and expected_result:
                    check_domfsinfo(domfsinfo, expected_result, test)
                else:
                    logging.debug("Virsh.domfsinfo output:\n%s", domfsinfo)
                    logging.debug("Expected_result is:\n%s", expected_result)
                    test.error("Command output inconsistent with expected")
                session.close()
    finally:
        if cleanup_nfs:
            libvirt.setup_or_cleanup_nfs(False, mount_dir=mount_dir)
        if vm.is_alive():
            vm.destroy()
        if hotplug_unplug:
            if disk_path:
                cmd = "rm -rf %s" % disk_path
                process.run(cmd)
        vmxml_backup.sync()
