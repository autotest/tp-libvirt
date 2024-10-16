#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Red Hat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Chunfu Wen<chwen@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

import logging
import os
import re

from avocado.utils import process

from virttest import utils_libvirtd
from virttest import virt_vm
from virttest import virt_admin

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_disk
from virttest.utils_test import libvirt
from virttest.staging import service


LOG = logging.getLogger('avocado.' + __name__)
cleanup_files = []


def config_journal_socket(params, test):
    """
    Configure journal socket

    :param params: wrapped parameters in dictionary format
    :param test: test assert object
    """
    enable_journal_socket = "yes" == params.get("enable_journal_socket", "no")

    socket_path = "/run/systemd/journal/socket"
    control_service = service.Factory.create_service("systemd-journald.socket")
    if enable_journal_socket:
        if not os.path.exists(socket_path):
            # generate journal socket
            control_service.restart()
    else:
        if os.path.exists(socket_path):
            # remove journal socket
            os.remove(socket_path)
        control_service.stop()
    utils_libvirtd.Libvirtd('virtqemud').restart()


def check_log_outputs(params, test):
    """
    Check whether virt-admin output contains expected log output

    :param params: wrapped parameters in dictionary format
    :param test: test assert object
    """
    log_output = params.get("log_outputs")
    vp = virt_admin.VirtadminPersistent()
    virt_admin_log = vp.daemon_log_outputs(ignore_status=True, debug=True).stdout_text.strip()
    if not re.search(r'%s' % log_output, virt_admin_log):
        test.fail("Can not find expected log output: %s from virt admin command output: %s" % (log_output, virt_admin_log))


def create_customized_disk(params):
    """
    Create one customized disk with related attributes

    :param params: dict wrapped with params
    :return: return disk device
    """
    type_name = params.get("type_name")
    device_target = params.get("target_dev")
    disk_device = params.get("device_type")
    device_bus = params.get("target_bus")
    device_format = params.get("target_format")
    source_file = params.get("virt_disk_device_source")
    source_dict = {}
    source_dict.update({"file": source_file})
    disk_src_dict = {"attrs": source_dict}

    libvirt.create_local_disk("file", source_file, 1, disk_format="qcow2")

    cleanup_files.append(source_file)

    customized_disk = libvirt_disk.create_primitive_disk_xml(
        type_name, disk_device,
        device_target, device_bus,
        device_format, disk_src_dict, None)

    LOG.debug("create customized xml: %s", customized_disk)
    return customized_disk


def generate_error_warning_log(params, test):
    """
    Generate warning or error logs

    :param params: wrapped parameters in dictionary format
    :param test: test assert object
    """
    vm_name = params.get("main_vm")
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    domain_file_path = "/var/lib/libvirt/qemu/save/%s.save" % vm_name
    # attach one disk xml
    disk_xml_no_source = create_customized_disk(params)
    vmxml.add_device(disk_xml_no_source)
    vmxml.sync()


def check_msg_in_var_log_message(params, test):
    """
    Check related message in /var/log/messages log file

    :param params: wrapped parameters in dictionary format
    :param test: test assert object
    """
    log_config_path = params.get("log_file_path")
    str_to_grep = params.get("str_to_grep")
    cmd = "grep -E -l '%s' %s" % (str_to_grep, log_config_path)
    if process.run(cmd, shell=True, ignore_status=True).exit_status != 0:
        test.fail("Check message log:%s failed in log file:%s" % (str_to_grep, log_config_path))


def run(test, params, env):
    """
    Test check virt-admin default log_outputs in various conditions

    1) Depend on whether /run/systemd/journal/socket exists or not;
    2) Restart libvirtd/virtqemud daemon;
    3) Check if the default log_outputs should be expected
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    # Clear log file
    log_config_path = params.get("log_file_path")
    truncate_log = "truncate -s 0 %s" % log_config_path
    process.run(truncate_log, ignore_status=True, shell=True, verbose=True)

    # Back up xml file
    if vm.is_alive():
        vm.destroy(gracefully=False)
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    try:
        config_journal_socket(params, test)
        check_log_outputs(params, test)
        generate_error_warning_log(params, test)
        vm.start()
    except virt_vm.VMStartError as e:
        LOG.debug("VM failed to start as expected."
                  "Error: %s", str(e))
        check_msg_in_var_log_message(params, test)
    finally:
        # Recover VM
        LOG.info("Restoring vm...")
        if vm.is_alive():
            vm.destroy(gracefully=False)
        vmxml_backup.sync()
        # Clean up files
        for file_path in cleanup_files:
            if os.path.exists(file_path):
                os.remove(file_path)
