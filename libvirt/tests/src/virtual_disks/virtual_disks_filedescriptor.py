#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Red Hat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Chunfu Wen <chwen@redhat.com>
#

import logging
import os
import platform
import shutil
import time

from avocado.utils import process

from virttest import libvirt_version
from virttest import utils_disk
from virttest import utils_misc
from virttest import utils_selinux
from virttest import virsh
from virttest import virt_vm


from virttest.libvirt_xml import vm_xml, xcepts
from virttest.utils_libvirt import libvirt_disk
from virttest.utils_test import libvirt

LOG = logging.getLogger('avocado.' + __name__)
cleanup_files = []


def get_added_disks(old_partitions, test, params, env):
    """
    Get new virtual disks in VM after disk plug.

    :param old_partitions: already existing partitions in VM
    :param test: test object
    :param params: one dictionary wrapping parameters
    :param env: environment representing running context
    :return: New disks/partitions in VM
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    session = None
    try:
        session = vm.wait_for_login()
        if platform.platform().count('ppc64'):
            time.sleep(10)
        added_partitions = utils_disk.get_added_parts(session, old_partitions)
        LOG.debug("Newly added partition(s) is: %s", added_partitions)
        return added_partitions
    except Exception as err:
        test.fail("Error happens when get new disk: %s" % str(err))
    finally:
        if session:
            session.close()


def create_customized_disk(params):
    """
    Create one customized disk with related attributes

    :param params: dict wrapped with params
    """
    type_name = params.get("type_name")
    disk_device = params.get("device_type")
    device_target = params.get("target_dev")
    device_bus = params.get("target_bus")
    device_format = params.get("target_format")
    fdgroup_name = params.get("fdgroup_name")
    source_file_path = params.get("source_file_path")
    source_dict = {}

    if source_file_path:
        libvirt.create_local_disk("file", source_file_path, 1, device_format)
        source_dict.update({"file": source_file_path, "fdgroup": fdgroup_name})
        cleanup_files.append(source_file_path)

    disk_src_dict = {"attrs": source_dict}
    customized_disk = libvirt_disk.create_primitive_disk_xml(
        type_name, disk_device,
        device_target, device_bus,
        device_format, disk_src_dict, None)
    if params.get("disk_readonly", "no") == "yes":
        customized_disk.readonly = True
    LOG.debug("create customized xml: %s", customized_disk)
    return customized_disk


def coldplug_disk(test, params, env):
    """
    cold plug disk, and start VM in one session

    :param test: test object
    :param params: one dictionary wrapping parameters
    :param env: environment representing running context
    """
    # associate file descriptor to domain is very special, and need all actions in one session
    vm_name = params.get("main_vm")
    fdgroup_name = params.get("fdgroup_name")
    file_path = params.get("source_file_path")
    file_descriptor_id = params.get("file_descriptor_id")
    flag = params.get("flag")
    start_cmd = "virsh \"dom-fd-associate %s %s %s %s; start %s\" %s<>%s" \
        % (vm_name, fdgroup_name, file_descriptor_id, flag, vm_name, file_descriptor_id, file_path)
    associate_fd_with_domain(start_cmd, test, params, env)


def coldplug_save_restore(test, params, env):
    """
    cold plug disk, save and restore VM in one session

    :param test: test object
    :param params: one dictionary wrapping parameters
    :param env: environment representing running context
    """
    # associate file descriptor to domain is very special, and need all actions in one session
    vm_name = params.get("main_vm")
    fdgroup_name = params.get("fdgroup_name")
    file_path = params.get("source_file_path")
    file_descriptor_id = params.get("file_descriptor_id")
    save_file_path = params.get("save_file_path")
    flag = params.get("flag")
    start_save_restore_cmd = "virsh \"dom-fd-associate %s %s %s %s ; start %s ; save %s %s ; restore %s\" %s<>%s" \
        % (vm_name, fdgroup_name, file_descriptor_id, flag, vm_name,
           vm_name, save_file_path, save_file_path, file_descriptor_id, file_path)
    associate_fd_with_domain(start_save_restore_cmd, test, params, env)


def associate_fd_with_domain(run_cmd, test, params, env):
    """
    associate file descriptor with domain

    :param run_cmd: command to be executed
    :param test: test object
    :param params: one dictionary wrapping parameters
    :param env: environment representing running context
    """
    command_output = process.run(
        run_cmd,
        timeout=10, ignore_status=False, verbose=True, shell=True)
    if command_output.exit_status != 0:
        test.fail("Fail to execute:" % run_cmd)
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vm.wait_for_login().close()


def hotplug_disk(test, params, env):
    """
    Hot plug one disk in one session

    :param test: test object
    :param params: one dictionary wrapping parameters
    :param env: environment representing running context
    """
    # associate file descriptor to domain is very special, and need all actions in one session
    vm_name = params.get("main_vm")
    fdgroup_name = params.get("fdgroup_name")
    file_path = params.get("source_file_path")
    file_descriptor_id = params.get("file_descriptor_id")
    target_device = params.get("target_dev")
    flag = params.get("flag")
    attach_disk_cmd = "virsh \"dom-fd-associate %s %s %s %s ; attach-disk %s %s %s;\" %s<>%s" \
        % (vm_name, fdgroup_name, file_descriptor_id, flag, vm_name,
           file_path, target_device, file_descriptor_id, file_path)
    associate_fd_with_domain(attach_disk_cmd, test, params, env)


def hotplug_save_restore(test, params, env):
    """
    Hot attach disk, save and restore VM

    :param test: test object
    :param params: one dictionary wrapping parameters
    :param env: environment representing running context
    """
    # associate file descriptor to domain is very special, and need all actions in one session
    vm_name = params.get("main_vm")
    fdgroup_name = params.get("fdgroup_name")
    file_path = params.get("source_file_path")
    file_descriptor_id = params.get("file_descriptor_id")
    target_device = params.get("target_dev")
    save_file_path = params.get("save_file_path")
    flag = params.get("flag")
    attach_save_restore_cmd = "virsh \"dom-fd-associate %s %s %s %s; attach-disk %s %s %s; \
        save %s %s ; restore %s\" %s<>%s" \
        % (vm_name, fdgroup_name, file_descriptor_id, flag, vm_name, file_path, target_device,
           vm_name, save_file_path, save_file_path, file_descriptor_id, file_path)
    associate_fd_with_domain(attach_save_restore_cmd, test, params, env)


def check_disk_file_selinux_label(test, params, check_phase):
    """
    Check disk security linux label

    :param test: test object
    :param params: one dictionary wrapping parameters
    :param check_phase: one flag indicating which phase is validated
    """
    source_file_path = params.get("source_file_path")
    svirt_disk_default_label = params.get("svirt_disk_default_label")
    svirt_disk_start_label = params.get("svirt_disk_start_label")
    svirt_disk_stop_label = params.get("svirt_disk_stop_label")
    if check_phase == "before_start":
        label = svirt_disk_default_label
    elif check_phase == "after_start":
        label = svirt_disk_start_label
    elif check_phase == "after_destroy":
        label = svirt_disk_stop_label

    LOG.debug("Now output phase:%s selinux label result" % check_phase)
    if not utils_selinux.check_context_of_file(source_file_path, label, selinux_force=True):
        test.fail("Get actual label: %s, but expected is : %s"
                  % (utils_selinux.get_context_of_file(source_file_path, selinux_force=True), label))


def hotplug_device(test, params, env):
    """
    Hot plug one device in one session

    :param test: test object
    :param params: one dictionary wrapping parameters
    :param env: environment representing running context
    """
    # associate file descriptor to domain is very special, and need all actions in one session
    vm_name = params.get("main_vm")
    fdgroup_name = params.get("fdgroup_name")
    file_path = params.get("source_file_path")
    file_descriptor_id = params.get("file_descriptor_id")
    device_xml = params.get("device_xml")
    flag = params.get("flag")
    attach_device_cmd = "virsh \"dom-fd-associate %s %s %s %s ; attach-device %s %s;\" %s<>%s" \
        % (vm_name, fdgroup_name, file_descriptor_id, flag, vm_name,
           device_xml, file_descriptor_id, file_path)
    associate_fd_with_domain(attach_device_cmd, test, params, env)


def run(test, params, env):
    """
    Test file descriptor disk.

    1.Prepare a vm with file descriptor disk
    2.Attach the virtual disk to the vm
    3.Start vm
    4.Check the disk in vm
    5.Detach disk device
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    hotplug = "yes" == params.get("virt_device_hotplug")
    pkgs_host = params.get("pkgs_host", "")
    disk_readonly = params.get("disk_readonly", "no") == "yes"
    part_path = "/dev/%s"

    # Skip test if version not match expected one
    libvirt_version.is_libvirt_feature_supported(params)

    # Get disk partitions info before hot/cold plug virtual disk
    if vm.is_dead():
        vm.start()
    session = vm.wait_for_login()
    old_partitions = utils_disk.get_parts_list(session)
    session.close()
    if not hotplug:
        vm.destroy(gracefully=False)

    # Backup vm xml
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    vmxml = vmxml_backup.copy()

    test_scenario = params.get("test_scenario")

    try:
        # install essential package in host
        if not shutil.which('lsof'):
            test.error("package {} not installed, please install them before testing".format(pkgs_host))

        device_obj = create_customized_disk(params)
        check_disk_file_selinux_label(test, params, "before_start")
        if not hotplug:
            vmxml.add_device(device_obj)
            vmxml.sync()
            if test_scenario == "attach_disk":
                coldplug_disk(test, params, env)
            elif test_scenario == "save_restore":
                coldplug_save_restore(test, params, env)
        else:
            if test_scenario == "attach_disk":
                hotplug_disk(test, params, env)
            elif test_scenario == "save_restore":
                hotplug_save_restore(test, params, env)
            elif test_scenario == "attach_device":
                params.update({'device_xml': device_obj.xml})
                hotplug_device(test, params, env)
    except virt_vm.VMStartError as details:
        test.fail("VM failed to start."
                  "Error: %s" % str(details))
    except xcepts.LibvirtXMLError as xml_error:
        test.fail("VM failed to define"
                  "Error: %s" % str(xml_error))
    else:
        check_disk_file_selinux_label(test, params, "after_start")
        utils_misc.wait_for(lambda: get_added_disks(old_partitions, test, params, env), 20)
        new_disks = get_added_disks(old_partitions, test, params, env)
        if len(new_disks) != 1:
            test.fail("Attached 1 virtual disk but got %s." % len(new_disks))
        new_disk = new_disks[0]
        if platform.platform().count('ppc64'):
            time.sleep(10)
        if disk_readonly:
            if libvirt_disk.check_virtual_disk_io(vm, new_disk, path=part_path):
                test.fail("Expect the newly added disk is not writable, but actually it is")
        else:
            if not libvirt_disk.check_virtual_disk_io(vm, new_disk, path=part_path):
                test.fail("Cannot operate the newly added disk in vm.")
        virsh.detach_device(vm_name, device_obj.xml, flagstr="--live",
                            debug=True, ignore_status=False)
        if vm.is_alive():
            vm.destroy(gracefully=False)
        check_disk_file_selinux_label(test, params, "after_destroy")
    finally:
        # Restoring vm
        vmxml_backup.sync()
        # Clean up files
        for file_path in cleanup_files:
            if os.path.exists(file_path):
                os.remove(file_path)
