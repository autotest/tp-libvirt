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
import re
import time

from avocado.utils import process

from virttest import libvirt_version
from virttest import virsh
from virttest import virt_vm

from virttest.libvirt_xml import vm_xml, xcepts
from virttest.utils_libvirt import libvirt_disk
from virttest.utils_test import libvirt

LOG = logging.getLogger('avocado.' + __name__)
cleanup_files = []


def create_customized_disk(params):
    """
    Create one customized disk with related attributes

    :param params: dict wrapped with params
    :return: disk if create successfully
    """
    type_name = params.get("type_name")
    disk_device = params.get("device_type")
    device_target = params.get("target_dev")
    device_bus = params.get("target_bus")
    device_format = params.get("target_format")
    discard_granularity = params.get("discard_granularity")
    source_file_path = params.get("source_file_path")
    sock_path = params.get("socket_file")
    source_dict = {}

    if source_file_path:
        libvirt.create_local_disk("file", source_file_path, 1, device_format)
        cleanup_files.append(source_file_path)
        source_dict.update({"file": source_file_path})
    elif sock_path:
        source_dict.update({"type": "unix", "path": sock_path})

    disk_src_dict = {"attrs": source_dict}
    customized_disk = libvirt_disk.create_primitive_disk_xml(
        type_name, disk_device,
        device_target, device_bus,
        device_format, disk_src_dict, None)
    if discard_granularity:
        customized_disk.blockio = {'logical_block_size': "512", "discard_granularity": discard_granularity}
    LOG.debug("create customized xml: %s", customized_disk)
    return customized_disk


def create_vhostuser_image_file(vhost_image_path):
    """
    Create vhostuser image file

    :param vhost_image_path: image file path
    """
    libvirt.create_local_disk("file", vhost_image_path, size="100M")
    chown_cmd = "chown qemu:qemu %s" % vhost_image_path
    process.run(chown_cmd, ignore_status=False, shell=True)


def start_vhost_sock_service(file_path, socket_path):
    """
    Start one vhost sock service

    :param file_path: image file path
    :param socket_path: socket file path
    :return: command output
    """
    start_sock_service_cmd = (
        'systemd-run --uid qemu --gid qemu /usr/bin/qemu-storage-daemon'
        ' --blockdev \'{"driver":"file","filename":"%s","node-name":"libvirt-1-storage","auto-read-only":true,"discard":"unmap"}\''
        ' --blockdev \'{"node-name":"libvirt-1-format","read-only":false,"driver":"raw","file":"libvirt-1-storage"}\''
        ' --export vhost-user-blk,id=vhost-user-blk0,node-name=libvirt-1-format,addr.type=unix,addr.path=%s,writable=on'
        ' --chardev stdio,mux=on,id=char0; sleep 3'
        % (file_path, socket_path))
    cmd_output = process.run(start_sock_service_cmd, ignore_status=False, shell=True).stdout_text.strip()
    ch_seccontext_cmd = "chcon -t svirt_image_t %s" % socket_path
    process.run(ch_seccontext_cmd, ignore_status=False, shell=True)
    set_bool_mmap_cmd = "setsebool domain_can_mmap_files 1 -P"
    process.run(set_bool_mmap_cmd, ignore_status=False, shell=True)
    return cmd_output


def check_blockio_discard_granularity(vm, new_disk, discard_granularity_in_unit):
    """
    Check disk discard granularity in guest internal

    :param vm: vm object
    :param new_disk: newly vm disk
    :param discard_granularity_in_unit: discard granularity in unit
    :return: boolean value indicating whether succeed or not
    """
    session = None
    try:
        session = vm.wait_for_login()
        cmd = ("lsblk --discard|grep {0}"
               .format(new_disk))
        status, output = session.cmd_status_output(cmd)
        LOG.debug("Disk operation in VM:\nexit code:\n%s\noutput:\n%s",
                  status, output)
        return discard_granularity_in_unit in output
    except Exception as err:
        LOG.debug("Error happens when check disk io in vm: %s", str(err))
        return False
    finally:
        if session:
            session.close()


def modify_line_with_pattern(file_path, pattern, replacement):
    """
    Modify line with pattern, then write back to file

    :param file_path: file path
    :param pattern: matched pattern
    :param replacement: replaced content
    """
    with open(file_path, 'r') as file:
        lines = file.readlines()
    for i, line in enumerate(lines):
        if re.search(pattern, line):
            lines[i] = re.sub(pattern, replacement, line)
            break
    with open(file_path, 'w') as file:
        file.writelines(lines)


def check_vm_dumpxml(params, test, expected_attribute=True):
    """
    Common method to check source in cdrom device

    :param params: one collective object representing wrapped parameters
    :param test: test object
    :param expected_attribute: bool indicating whether expected attribute exists or not
    """
    vm_name = params.get("main_vm")
    disk_vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    target_dev = params.get('target_dev')
    disk = disk_vmxml.get_disk_all()[target_dev]
    actual_discard_granularity = disk.find('blockio').get('discard_granularity')
    if not expected_attribute:
        if actual_discard_granularity is not None:
            test.fail("unexpected value for blockio in VM disk XML")
    else:
        if actual_discard_granularity is None:
            test.fail("discard_granularity can not be found in vm disk xml")
        else:
            expected_discard_granularity = params.get("discard_granularity")
            if actual_discard_granularity != expected_discard_granularity:
                test.fail("actual discard_granularity: %s is not equal to expected: %s" % (actual_discard_granularity, expected_discard_granularity))
            else:
                test.log.debug("Get expected discard_granularity: %s" % actual_discard_granularity)


def run(test, params, env):
    """
    Test disk with discard_granularity.

    1.Prepare a vm with discard_granularity disk
    2.Attach the virtual disk to the vm
    3.Start vm
    4.Check discard_granularity in vm
    5.Detach disk device
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    hotplug = "yes" == params.get("virt_device_hotplug")
    part_path = "/dev/%s"

    # Skip test if version not match expected one
    libvirt_version.is_libvirt_feature_supported(params)

    if vm.is_alive():
        vm.destroy(gracefully=False)

    # Backup vm xml
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    vmxml = vmxml_backup.copy()

    test_scenario = params.get("test_scenario")
    vhost_source_file_path = params.get("vhost_source_file_path")
    discard_granularity_in_unit = params.get("discard_granularity_in_unit")

    vsock_service_id = None
    target_bus = params.get("target_bus")
    status_error = "yes" == params.get("status_error")
    define_error = "yes" == params.get("define_error")

    try:
        # For vhost user disk, it need setup socket first
        if vhost_source_file_path:
            create_vhostuser_image_file(vhost_source_file_path)
            socket_path = params.get("socket_file")
            vsock_service_id = start_vhost_sock_service(vhost_source_file_path, socket_path)
        # For invalid discard granularity test, it need update value accordingly
        elif test_scenario in ["failure_vm_start"]:
            params.update({'discard_granularity': params.get('invalid_discard_granularity')})

        device_obj = create_customized_disk(params)
        if test_scenario in ["live_update_discard_granularity"]:
            device_obj_backup = device_obj.copy()
            new_discard_granularity = params.get('new_discard_granularity')
            discard_granularity = params.get('discard_granularity')
            pattern = r'discard_granularity="%s"' % discard_granularity
            replacement = 'discard_granularity="%s"' % new_discard_granularity
            modify_line_with_pattern(device_obj_backup.xml, pattern, replacement)
        if not hotplug:
            vmxml.add_device(device_obj)
            vmxml.sync()
        vm.start()
        vm.wait_for_login()
        if hotplug:
            virsh.attach_device(vm_name, device_obj.xml,
                                ignore_status=False, debug=True)
    except xcepts.LibvirtXMLError as xml_error:
        if not define_error:
            test.fail("Failed to define VM:\n%s" % str(xml_error))
        else:
            item_matched = params.get("define_error_msg")
            if not re.search(r'%s' % item_matched, str(xml_error)):
                test.fail("Get unexpected define error message from: %s" % str(xml_error))
    except virt_vm.VMStartError as details:
        if not status_error:
            test.fail("VM failed to start."
                      "Error: %s" % str(details))
        else:
            status_error_msg = params.get("status_error_msg")
            if not re.search(r'%s' % status_error_msg, str(details)):
                test.fail("Get unexpected define error message from: %s" % str(details))
    else:
        session = vm.wait_for_login()
        time.sleep(20)
        new_disk, _ = libvirt_disk.get_non_root_disk_name(session)
        session.close()

        if not libvirt_disk.check_virtual_disk_io(vm, new_disk, path=part_path):
            test.fail("fail to execute write operations on newly added disk:%s" % new_disk)
        if test_scenario in ["live_update_discard_granularity"]:
            result = virsh.update_device(vm_name, device_obj_backup.xml, flagstr="--live",
                                         debug=True, ignore_status=True)
            error_msg = params.get("error_msg")
            libvirt.check_result(result, error_msg)
        elif test_scenario in ["boundary_vm_start"]:
            expected_attribute = "yes" == params.get("expected_attribute", "no")
            discard_granularity = params.get("discard_granularity")
            if discard_granularity == '0':
                if libvirt_version.version_compare(11, 7, 0):
                    expected_attribute = True
                else:
                    expected_attribute = False
            check_vm_dumpxml(params, test, expected_attribute)
        else:
            if not check_blockio_discard_granularity(vm, new_disk, discard_granularity_in_unit):
                test.fail("fail to check discard granularity on newly added disk:%s" % new_disk)
        if hotplug:
            virsh.detach_device(vm_name, device_obj.xml, flagstr="--live",
                                debug=True, ignore_status=False)
    finally:
        if vm.is_alive():
            vm.destroy(gracefully=False)
        # Restoring vm
        vmxml_backup.sync()

        if vhost_source_file_path:
            # Kill all qemu-storage-daemon process on host
            process.run("pidof qemu-storage-daemon && killall qemu-storage-daemon",
                        ignore_status=True, verbose=True, shell=True)

        if vsock_service_id:
            stop_vsock_service_cmd = "systemctl stop %s" % vsock_service_id
            process.run(stop_vsock_service_cmd, ignore_status=True, verbose=True, shell=True)
        # Clean up files
        for file_path in cleanup_files:
            if os.path.exists(file_path):
                os.remove(file_path)
