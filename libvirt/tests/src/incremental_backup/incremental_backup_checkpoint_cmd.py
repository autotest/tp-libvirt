import os
import re
import logging
import operator
import xml.etree.ElementTree as ET

from virttest import virsh
from virttest import data_dir
from virttest import libvirt_version
from virttest import utils_disk
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml import checkpoint_xml
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Test pure checkpoint commands
    """

    def prepare_checkpoints(disk="vdb", num=1, cp_prefix="test_checkpoint_"):
        """
        Create checkpoints for specific disk

        :param disk: The disk to create checkpoint.
        :param num: How many checkpoints to be created
        :param cp_prefix: The prefix to name the checkpoint.
        """
        option_pattern = ("{0} --diskspec vda,checkpoint=no "
                          "--diskspec {1},checkpoint=bitmap,bitmap={0}")
        for i in range(num):
            # create checkpoints
            checkpoint_name = cp_prefix + str(i)
            options = option_pattern.format(checkpoint_name, disk)
            virsh.checkpoint_create_as(vm_name, options, **virsh_dargs)
            current_checkpoints.append(checkpoint_name)

    # Cancel the test if libvirt version is too low
    if not libvirt_version.version_compare(6, 0, 0):
        test.cancel("Current libvirt version doesn't support "
                    "incremental backup.")

    checkpoint_cmd = params.get("checkpoint_cmd")
    cmd_flag = params.get("flag")
    required_checkpoints = int(params.get("required_checkpoints", 0))
    test_disk_size = params.get("test_disk_size", "100M")
    test_disk_target = params.get("test_disk_target", "vdb")
    status_error = "yes" == params.get("status_error")
    tmp_dir = data_dir.get_tmp_dir()
    current_checkpoints = []
    virsh_dargs = {'debug': True, 'ignore_status': False}

    try:
        vm_name = params.get("main_vm")
        vm = env.get_vm(vm_name)

        # Backup vm xml
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        vmxml_backup = vmxml.copy()

        # Enable vm incremental backup capability. This is only a workaround
        # to make sure incremental backup can work for the vm. Code needs to
        # be removded immediately when the function enabled by default, which
        # is tracked by bz1799015
        tree = ET.parse(vmxml.xml)
        root = tree.getroot()
        for elem in root.iter('domain'):
            elem.set('xmlns:qemu', 'http://libvirt.org/schemas/domain/qemu/1.0')
            qemu_cap = ET.Element("qemu:capabilities")
            elem.insert(-1, qemu_cap)
            incbackup_cap = ET.Element("qemu:add")
            incbackup_cap.set('capability', 'incremental-backup')
            qemu_cap.insert(1, incbackup_cap)
        vmxml.undefine()
        tmp_vm_xml = os.path.join(tmp_dir, "tmp_vm.xml")
        tree.write(tmp_vm_xml)
        virsh.define(tmp_vm_xml)
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        logging.debug("Script insert xml elements to make sure vm can support "
                      "incremental backup. This should be removded when "
                      "bz 1799015 fixed.")
        if vm.is_alive():
            vm.destroy(gracefully=False)
        # Prepare the disk to be used.
        disk_params = {}
        disk_path = ""
        image_name = "{}_image.qcow2".format(test_disk_target)
        disk_path = os.path.join(tmp_dir, image_name)
        libvirt.create_local_disk("file", disk_path, test_disk_size,
                                  "qcow2")
        disk_params = {"device_type": "disk",
                       "type_name": "file",
                       "driver_type": "qcow2",
                       "target_dev": test_disk_target,
                       "source_file": disk_path}
        disk_xml = libvirt.create_disk_xml(disk_params)
        virsh.attach_device(vm.name, disk_xml,
                            flagstr="--config", **virsh_dargs)
        vm.start()
        session = vm.wait_for_login()
        new_disks_in_vm = list(utils_disk.get_linux_disks(session).keys())
        session.close()
        if required_checkpoints > 0:
            prepare_checkpoints(test_disk_target, required_checkpoints)
        if checkpoint_cmd == "checkpoint-create":
            if not current_checkpoints:
                test.fail("No existing checkpoints prepared.")
            if "--redefine" in cmd_flag:
                no_domain = "yes" == params.get("no_domain")
                cp_dumpxml_options = ""
                if no_domain:
                    cp_dumpxml_options = "--no-domain"
                checkpoint_redef = current_checkpoints[0]
                cp_xml = checkpoint_xml.CheckpointXML.new_from_checkpoint_dumpxml(
                        vm_name, checkpoint_redef, cp_dumpxml_options)
                logging.debug("Checkpoint XML to be redefined is: %s", cp_xml)
                xml_file = cp_xml.xml
                virsh.checkpoint_delete(vm_name, checkpoint_redef,
                                        "--metadata", **virsh_dargs)
                cmd_options = xml_file + " " + cmd_flag
                result = virsh.checkpoint_create(vm_name, cmd_options, debug=True)
                libvirt.check_exit_status(result, status_error)
        elif checkpoint_cmd == "checkpoint-create-as":
            if "--print-xml" in cmd_flag:
                checkpoint_name = "test_checkpoint_0"
                options = ("{0} --diskspec vda,checkpoint=no --diskspec {1},"
                           "checkpoint=bitmap,bitmap={0} "
                           "--print-xml".format(checkpoint_name, test_disk_target))
                virsh.checkpoint_create_as(vm_name, options, **virsh_dargs)
                # The checkpiont should not be created, so we have following check
                cp_list_result = virsh.checkpoint_list(vm_name, checkpoint_name, debug=True)
                libvirt.check_exit_status(cp_list_result, True)
        elif checkpoint_cmd == "checkpoint-info":
            if len(current_checkpoints) != 3:
                test.fail("We should prepare 3 checkpoints.")
            parent_checkpoint = current_checkpoints[0]
            test_checkpoint = current_checkpoints[1]
            stdout = virsh.checkpoint_info(vm_name, test_checkpoint,
                                           **virsh_dargs).stdout_text.strip()
            if (
                    not re.search("domain.*%s" % vm_name, stdout, re.IGNORECASE) or
                    not re.search("parent.*%s" % parent_checkpoint, stdout, re.IGNORECASE) or
                    not re.search("children.*1", stdout, re.IGNORECASE) or
                    not re.search("descendants.*1", stdout, re.IGNORECASE)
               ):
                test.fail("checkpoint-info return inaccurate informaion: %s" % stdout)
        elif checkpoint_cmd == "checkpoint-list":
            logic_error = False
            if not cmd_flag:
                stdout = virsh.checkpoint_list(vm_name,
                                               **virsh_dargs).stdout_text.strip()
                for checkpoint in current_checkpoints:
                    if checkpoint not in stdout:
                        logic_error = True
            elif cmd_flag == "--parent":
                stdout = virsh.checkpoint_list(vm_name, cmd_flag,
                                               **virsh_dargs).stdout_text.strip()
                for checkpoint in current_checkpoints:
                    if checkpoint == current_checkpoints[-1]:
                        if stdout.count(checkpoint) != 1:
                            logic_error = True
                    else:
                        if stdout.count(checkpoint) != 2:
                            logic_error = True
            elif cmd_flag == "--roots":
                stdout = virsh.checkpoint_list(vm_name, cmd_flag,
                                               **virsh_dargs).stdout_text.strip()
                for checkpoint in current_checkpoints:
                    if checkpoint == current_checkpoints[0]:
                        if stdout.count(checkpoint) != 1:
                            logic_eror = True
                    else:
                        if stdout.count(checkpoint) != 0:
                            logic_error = True
            elif cmd_flag == "--tree":
                stdout = virsh.checkpoint_list(vm_name, cmd_flag,
                                               **virsh_dargs).stdout_text.strip()
                lines = stdout.splitlines()
                prev_indent_num = -1
                for line in lines:
                    for checkpoint in current_checkpoints:
                        if checkpoint in line:
                            cur_indent_num = line.rstrip().count(" ")
                            if cur_indent_num <= prev_indent_num:
                                logic_error = True
                                break
                            prev_indent_num = cur_indent_num
            elif cmd_flag == "--name":
                stdout = virsh.checkpoint_list(vm_name, cmd_flag,
                                               **virsh_dargs).stdout_text.strip()
                checkpoint_names = stdout.splitlines()
                if not operator.eq(checkpoint_names, current_checkpoints):
                    logic_error = True
            elif cmd_flag == "--topological":
                stdout = virsh.checkpoint_list(vm_name, cmd_flag,
                                               **virsh_dargs).stdout_text.strip()
                for checkpoint in current_checkpoints:
                    if checkpoint not in stdout:
                        logical_error = True
            elif cmd_flag == "--from":
                cmd_options = cmd_flag + " " + current_checkpoints[0]
                stdout = virsh.checkpoint_list(vm_name, cmd_options,
                                               **virsh_dargs).stdout_text.strip()
                if (current_checkpoints[0] in stdout
                        or current_checkpoints[2] in stdout
                        or current_checkpoints[1] not in stdout):
                    logic_error = True
            elif cmd_flag == "--descendants":
                cmd_options = cmd_flag + " " + current_checkpoints[0]
                stdout = virsh.checkpoint_list(vm_name, cmd_options,
                                               **virsh_dargs).stdout_text.strip()
                if (current_checkpoints[0] in stdout
                        or current_checkpoints[1] not in stdout
                        or current_checkpoints[2] not in stdout):
                    logic_error = True
            elif cmd_flag == "--no-leaves":
                stdout = virsh.checkpoint_list(vm_name, cmd_flag,
                                               **virsh_dargs).stdout_text.strip()
                if (current_checkpoints[0] not in stdout
                        or current_checkpoints[1] not in stdout
                        or current_checkpoints[2] in stdout):
                    logic_error = True
            elif cmd_flag == "--leaves":
                stdout = virsh.checkpoint_list(vm_name, cmd_flag,
                                               **virsh_dargs).stdout_text.strip()
                if (current_checkpoints[0] in stdout
                        or current_checkpoints[1] in stdout
                        or current_checkpoints[2] not in stdout):
                    logic_error = True
            if logic_error:
                test.fail("checkpoint-list with '%s' gives wrong output"
                          % cmd_flag)
        elif checkpoint_cmd == "checkpoint-dumpxml":
            if "--size" in cmd_flag:
                if not libvirt_version.version_compare(6, 6, 0):
                    test.cancel("Current libvirt version doesn't support "
                                "'--size' for 'checkpoint-dumpxml'.")
                test_disk = new_disks_in_vm[-1]
                test_disk_path = "/dev/" + test_disk
                test_checkpoint = current_checkpoints[-1]
                dd_count = 1
                dd_bs = "1M"
                dd_seek = "10"
                dd_size = dd_count * 1024 * 1024
                session = vm.wait_for_login()
                utils_disk.dd_data_to_vm_disk(session, test_disk_path,
                                              bs=dd_bs, seek=dd_seek,
                                              count=str(dd_count))
                session.close()
                stdout = virsh.checkpoint_dumpxml(vm_name,
                                                  test_checkpoint + " --size",
                                                  **virsh_dargs).stdout_text.strip()
                re_pattern = ".*%s.*%s.*size.*" % (test_disk, test_checkpoint)
                size_info_line = re.search(re_pattern, stdout)
                if not size_info_line:
                    test.fail("There is no size info for disk:%s checkpoint:%s"
                              % (test_disk, test_checkpoint))
                if str(dd_size) not in size_info_line.group(0):
                    test.fail("Size info incorrect in checkpoint xml, "
                              "'dd_size' is %s, size info in xml is:%s"
                              % (dd_size, size_info_line.group(0)))
            elif "--security-info" in cmd_flag:
                if vm.is_alive():
                    vm.destroy(gracefully=False)
                password = "xyzxyzabcabc"
                vm_xml.VMXML.set_graphics_attr(vm_name, {'passwd': password})
                vm.start()
                vm.wait_for_login().close()
                prepare_checkpoints()
                test_checkpoint = current_checkpoints[0]
                stdout = virsh.checkpoint_dumpxml(vm_name,
                                                  test_checkpoint,
                                                  **virsh_dargs).stdout_text.strip()
                if password in stdout:
                    logging.debug("checkpoint xml is: %s", stdout)
                    test.fail("Security info displayed in unsecurity dumpxml.")
                stdout = virsh.checkpoint_dumpxml(vm_name,
                                                  test_checkpoint + " --security-info",
                                                  **virsh_dargs).stdout_text.strip()
                if password not in stdout:
                    logging.debug("checkpoint xml is: %s", stdout)
                    test.fail("Security info not displayed in security dumpxml.")
        elif checkpoint_cmd == "virsh_list":
            stdout = virsh.dom_list(cmd_flag, **virsh_dargs).stdout_text.strip()
            if ((vm_name in stdout and cmd_flag == "--without-checkpoint") or
                    (vm_name not in stdout and cmd_flag == "--with-checkpoint")):
                test.fail("virsh list with '%s' contains wrong data" % cmd_flag)
    finally:
        # Remove checkpoints
        if "current_checkpoints" in locals() and current_checkpoints:
            for checkpoint_name in current_checkpoints:
                virsh.checkpoint_delete(vm_name, checkpoint_name,
                                        ignore_status=True, debug=True)

        if vm.is_alive():
            vm.destroy(gracefully=False)

        # Restoring vm
        vmxml_backup.sync()
