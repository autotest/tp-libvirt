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

from avocado.utils import process

from virttest import virt_vm
from virttest import virsh

from virttest.libvirt_xml import pool_xml
from virttest.libvirt_xml import vm_xml, xcepts
from virttest.libvirt_xml.devices.hostdev import Hostdev
from virttest.utils_test import libvirt

from virttest.utils_libvirt import libvirt_disk
from virttest.utils_disk import get_scsi_info

LOG = logging.getLogger('avocado.' + __name__)
cleanup_vms = []
cleanup_files = []
device_info = None
detach_hostdev_xml = None


def setup_iscsi_block_device():
    """
    Setup one iscsi block device

    :return: device name
    """
    blk_dev = libvirt.setup_or_cleanup_iscsi(is_setup=True,
                                             is_login=True)
    return blk_dev


def setup_scsi_debug_block_device():
    """
    Setup one scsi_debug block device

    :return: device name
    """
    source_disk = libvirt.create_scsi_disk(scsi_option="",
                                           scsi_size="100")
    return source_disk


def setup_scsi_debug_tap_device():
    """
    Setup one scsi_debug tap device

    :return: device name
    """
    source_tap = libvirt.create_scsi_disk(scsi_option="ptype=1",
                                          scsi_size="100")
    return source_tap


def setup_iscsi_lun():
    """
    Setup iscsi lun

    :return: The iscsi target and lun number.
    """
    iscsi_target, lun_num = libvirt.setup_or_cleanup_iscsi(is_setup=True,
                                                           is_login=False,
                                                           chap_user="",
                                                           chap_passwd="",
                                                           portal_ip="127.0.0.1")
    return iscsi_target, lun_num


def create_host_dev(params, block_device):
    """
    Create hostdev xml

    :param params: parameters wrapped in dictionary
    :param block_device: block device path
    """
    alias_name = params.get("alias_name")
    boot_order = params.get("boot_order")
    pci_addr = get_scsi_info(block_device)
    hostdev_xml = libvirt.create_hostdev_xml(pci_addr, boot_order, "scsi", "no", alias_name, None)
    return hostdev_xml


def create_iscsi_target_host_dev(iscsi_target, lun_num):
    """
    Create iscsi target hostdev xml

    :param iscsi_target: block device path
    :param lun_num: iscsi lun number
    """
    hostdev_xml = Hostdev()
    hostdev_xml.mode = "subsystem"
    hostdev_xml.managed = "yes"
    hostdev_xml.type = "scsi"

    source_args = {'protocol': 'iscsi',
                   'source_name': iscsi_target + "/%s" % lun_num,
                   'host_name': '127.0.0.1',
                   'host_port': '3260'}
    hostdev_xml.source = hostdev_xml.new_source(**source_args)
    hostdev_xml.xmltreefile.write()
    LOG.info("hostdev xml is: %s", hostdev_xml)
    return hostdev_xml


def create_controller(params):
    """
    One method is to create and add one controller into VM xml

    :param params: dict wrapped with params
    """
    vm_name = params.get("main_vm")
    xml_dumped = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    controller_instance = libvirt.create_controller_xml(params)
    driver_dict = {'max_sectors': params.get('max_sectors')}
    if params.get('max_sectors'):
        controller_instance.driver = driver_dict
    LOG.info("controller xml is: %s", controller_instance)
    xml_dumped.add_device(controller_instance)
    xml_dumped.sync()
    return controller_instance


def add_one_disk(params):
    """
    Add one disk in VM xml

    :param params: dict wrapped with params
    """
    # Prepare disk source xml
    vm_name = params.get("main_vm")
    xml_dump = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    p_xml = pool_xml.PoolXML.new_from_dumpxml("images")
    source_file_path = os.path.join(p_xml.target_path, "testscsi.qcow2")
    params.update({"source_file_path": source_file_path})
    libvirt.create_local_disk("file", source_file_path, 1,
                              disk_format="qcow2")
    virsh.pool_refresh("images")
    cleanup_files.append(source_file_path)

    disk_src_dict = {"attrs": {"file": source_file_path}}
    target_device = params.get("target_device")

    customized_disk = libvirt_disk.create_primitive_disk_xml(
        "file", "disk",
        target_device, 'scsi',
        'qcow2', disk_src_dict, None)

    LOG.info("disk xml is: %s", customized_disk)
    xml_dump.add_device(customized_disk)
    xml_dump.sync()


def update_vm_boot_order(vm_name, disk_boot_index):
    """
    Update boot order of vm before test

    :param vm_name: vm name
    :param disk_boot_index: boot order index of disk
    """
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    # Remove os boot config to avoid conflict with boot setting of disk device
    vm_os = vmxml.os
    vm_os.del_boots()
    vmxml.os = vm_os
    disk = vmxml.get_devices('disk')[0]
    target_dev = disk.target.get('dev', '')
    logging.debug('Will set boot order %s to device %s',
                  disk_boot_index, target_dev)
    vmxml.set_boot_order_by_target_dev(target_dev, disk_boot_index)
    vmxml.sync()


def test_coldplug_scsi_hostdev_alias(test, params, env):
    """
    Test coldplug scsi hostdev with alias in VM

    :param test: one test object instance
    :param params: dict wrapped with params
    :param env: environment instance
    """
    vm_name = params.get("main_vm")
    block_device = setup_iscsi_block_device()
    hostdev_xml = create_host_dev(params, block_device)
    virsh.attach_device(vm_name, hostdev_xml.xml, flagstr="--config",
                        ignore_status=False)


def test_coldplug_scsi_hostdev_boot_order(test, params, env):
    """
    Test hostdev with boot order as preset

    :param test: one test object instance
    :param params: dict wrapped with params
    :param env: environment instance
    """
    vm_name = params.get("main_vm")
    update_vm_boot_order(vm_name, "1")
    block_device1 = setup_iscsi_block_device()
    params.update({'alias_name': params.get('alias_name_1')})
    params.update({'boot_order': params.get('boot_order_1')})

    hostdev_xml1 = create_host_dev(params, block_device1)
    virsh.attach_device(vm_name, hostdev_xml1.xml, flagstr="--config",
                        ignore_status=False)

    block_device2 = setup_scsi_debug_block_device()
    params.update({'alias_name': params.get('alias_name_2')})
    params.update({'boot_order': params.get('boot_order_2')})
    hostdev_xml2 = create_host_dev(params, block_device2)
    virsh.attach_device(vm_name, hostdev_xml2.xml, flagstr="--config",
                        ignore_status=False)


def check_scsi_device_boot_order(test, params, env):
    """
    Check VM scsi device boot order

    :param test: one test object instance
    :param params: dict wrapped with params
    :param env: environment instance
    """
    vm_name = params.get("main_vm")

    match_alias_plus_boot_order1 = '("%s","bootindex":%s)|(%s,bootindex=%s)'\
                                   % (params.get("alias_name_1"),
                                      params.get("boot_order_1"),
                                      params.get("alias_name_1"),
                                      params.get("boot_order_1"))
    libvirt.check_qemu_cmd_line(match_alias_plus_boot_order1)
    match_alias_plus_boot_order2 = '("%s","bootindex":%s)|(%s,bootindex=%s)'\
                                   % (params.get("alias_name_2"),
                                      params.get("boot_order_2"),
                                      params.get("alias_name_2"),
                                      params.get("boot_order_2"))
    libvirt.check_qemu_cmd_line(match_alias_plus_boot_order2)
    LOG.debug('boot order dumpxml:\n')
    LOG.debug(vm_xml.VMXML.new_from_dumpxml(vm_name))


def test_hotplug_scsi_hostdev_same_hostdev_address(test, params, env):
    """
    Test hotplug two hostdev with same address into VM

    :param test: one test object instance
    :param params: dict wrapped with params
    :param env: environment instance
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    if vm.is_dead():
        vm.start()
        vm.wait_for_login().close()
    block_device1 = setup_iscsi_block_device()
    hostdev_xml1 = create_host_dev(params, block_device1)
    virsh.attach_device(vm_name, hostdev_xml1.xml, flagstr="--config",
                        ignore_status=False)

    block_device2 = setup_scsi_debug_block_device()
    hostdev_xml2 = create_host_dev(params, block_device2)
    virsh.attach_device(vm_name, hostdev_xml2.xml, flagstr="--config",
                        ignore_status=False)


def check_hostdev_xml(test, params, env):
    """
    Check VM hostdev xml info

    :param test: one test object instance
    :param params: dict wrapped with params
    :param env: environment instance
    """
    vm_name = params.get("main_vm")
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    total_addr_unit = []
    vm_hostdevs = vmxml.devices.by_device_tag("hostdev")
    if len(vm_hostdevs) != 2:
        test.fail("should get two hostdev, but actually get %s" % len(vm_hostdevs))
    for hostdev in vm_hostdevs:
        addr_attr_dict = hostdev.address.attrs
        for key, value in addr_attr_dict.items():
            if key == "unit":
                total_addr_unit.append(value)
    if len(set(total_addr_unit)) != len(total_addr_unit):
        test.fail("hostdev: %s has same address unit, but should not" % total_addr_unit)


def test_hotplug_scsi_hostdev_tap_library(test, params, env):
    """
    Test hotplug one tap library into VM

    :param test: one test object instance
    :param params: dict wrapped with params
    :param env: environment instance
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    if vm.is_dead():
        vm.start()
        vm.wait_for_login().close()
    block_device = setup_scsi_debug_tap_device()
    params.update({'block_device': block_device})
    hostdev_xml = create_host_dev(params, block_device)
    global detach_hostdev_xml
    detach_hostdev_xml = hostdev_xml.copy()
    virsh.attach_device(vm_name, hostdev_xml.xml, flagstr="--live",
                        ignore_status=False)


def check_tap_library_device(test, params, env):
    """
    Check tap device in VM internal

    :param test: one test object instance
    :param params: dict wrapped with params
    :param env: environment instance
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    session = vm.wait_for_login()
    cmd_s, cmd_o = session.cmd_status_output("lsscsi|grep scsi_debug|awk '{print $6}'")
    block_device = params.get("block_device")
    if cmd_s != 0:
        test.error("can not login VM using session")
    if block_device not in cmd_o:
        test.fail("can not find expected tap device from VM internal:%s" % cmd_o)
    virsh.detach_device(vm_name, detach_hostdev_xml.xml, flagstr="--live",
                        ignore_status=False)
    _, cmd_output = session.cmd_status_output("lsscsi|grep scsi_debug|awk '{print $6}'")
    if block_device in cmd_output:
        test.fail("Find  unexpected tap device from VM internal:%s" % cmd_output)
    session.close()


def test_coldplug_scsi_hostdev_vdisk_hostdev_without_address(test, params, env):
    """
    Test coldplug one qcow2 image and hostdev with no address into VM

    :param test: one test object instance
    :param params: dict wrapped with params
    :param env: environment instance
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    add_one_disk(params)
    iscsi_target, lun_num = setup_iscsi_lun()
    hostdev_xml1 = create_iscsi_target_host_dev(iscsi_target, lun_num)
    virsh.attach_device(vm_name, hostdev_xml1.xml, flagstr="--config",
                        ignore_status=False)


def check_vdisk_hostdev_address_unit(test, params, env):
    """
    Check virtual disk and hostdev have different address unit

    :param test: one test object instance
    :param params: dict wrapped with params
    :param env: environment instance
    """
    vm_name = params.get("main_vm")
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    # Get hostdev address unit
    hostdev_addr_unit = None
    vm_hostdevs = vmxml.devices.by_device_tag("hostdev")
    addr_attr_dict = vm_hostdevs[0].address.attrs
    for key, value in addr_attr_dict.items():
        if key == "unit":
            hostdev_addr_unit = value
    # Get virtual disk address unit
    vdisk_addr_unit = vmxml.get_disk_attr(vm_name, params.get("target_device"), 'address', 'unit')
    if hostdev_addr_unit == vdisk_addr_unit:
        test.fail("hostdev: %s has same address unit with vdisk %s, but should not"
                  % (hostdev_addr_unit, vdisk_addr_unit))


def test_coldplug_scsi_hostdev_max_sectors_controller(test, params, env):
    """
    Test coldplug controller with max sectors into VM

    :param test: one test object instance
    :param params: dict wrapped with params
    :param env: environment instance
    """
    setup_iscsi_block_device()
    create_controller(params)


def test_hotplug_scsi_hostdev_unplug_scsi_controller(test, params, env):
    """
    Test hotplug/unplug scsi controller

    :param test: one test object instance
    :param params: dict wrapped with params
    :param env: environment instance
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    vmxml.del_controller(controller_type='scsi')
    vmxml.sync()
    scsi_xml = create_controller(params)
    if vm.is_dead():
        vm.start()
        vm.wait_for_login().close()
    virsh.detach_device(vm_name, scsi_xml.xml, flagstr="--live",
                        ignore_status=False)


def check_scsi_controller(test, params, env):
    """
    check scsi controller with index=0

    :param test: one test object instance
    :param params: dict wrapped with params
    :param env: environment instance
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    controllers = vmxml.get_devices(device_type="controller")
    for dev in controllers:
        if dev.type == "virtio-scsi" and dev.index == "0":
            test.fail("Get index=0 scsi controller although detached")


def test_hotplug_scsi_hostdev_shared_by_two_guests(test, params, env):
    """
    Test hotplug scsi device into two guests

    :param test: one test object instance
    :param params: dict wrapped with params
    :param env: environment instance
    """
    block_device1 = setup_iscsi_block_device()
    hostdev_xml = create_host_dev(params, block_device1)
    hostdev_xml.shareable = True
    vm_names = params.get("vms").split()
    for vm_name in vm_names:
        vm = env.get_vm(vm_name)
        if vm.is_dead():
            vm.start()
        vm.wait_for_login().close()
        virsh.attach_device(vm_name, hostdev_xml.xml, flagstr="--live",
                            ignore_status=False)


def check_hostdev_shareable_attr(test, params):
    """
    check scsi shareable attribute

    :param test: one test object instance
    :param params: dict wrapped with params
    """
    vm_names = params.get("vms").split()
    for vm_name in vm_names:
        vmxml_checked = vm_xml.VMXML.new_from_dumpxml(vm_name)
        hostdev_xml = vmxml_checked.get_devices('hostdev')[0]
        shareable_attr = hostdev_xml.shareable
        if shareable_attr is not True:
            test.fail("VM: % failed to find shareable attribute in output: %s" % (vm_name, str(hostdev_xml)))


def test_coldplug_scsi_hostdev_qemu_pr_helper(test, params):
    """
    Test coldplug scsi hostdev and check qemu-pr-helper status

    :param test: one test object instance
    :param params: dict wrapped with params
    """
    vm_name = params.get("main_vm")
    block_device = setup_scsi_debug_block_device()

    disk_src_dict = {"attrs": {"dev": block_device}}
    target_device = params.get("target_device")

    customized_disk = libvirt_disk.create_primitive_disk_xml(
        "block", "lun",
        target_device, 'scsi',
        'raw', disk_src_dict, None)

    # update reservation attributes
    reservations_dict = {"reservations_managed": "yes"}
    disk_source = customized_disk.source
    disk_source.reservations = customized_disk.new_reservations(**reservations_dict)
    customized_disk.source = disk_source

    LOG.info("disk xml is: %s", customized_disk)
    xml_dump = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    xml_dump.add_device(customized_disk)
    xml_dump.sync()


def check_qemu_pr_helper(test, params, env):
    """
    check qemu_qr_helper process can be restarted when VM issue pr cmds

    :param test: one test object instance
    :param params: dict wrapped with params
    :param env: environment instance
    """
    old_qr_pid = process.run("pidof qemu-pr-helper",
                             ignore_status=True, shell=True).stdout_text.strip()
    if old_qr_pid is None:
        test.fail("qemu-pr-helper is not started after VM is started")
    process.system("killall qemu-pr-helper && sleep 2",
                   ignore_status=True, shell=True)

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    session = vm.wait_for_login()
    _, cmd_o = session.cmd_status_output("lsscsi|grep scsi_debug|awk '{print $6}'")
    # send series of pr commands to VM
    sg_cmd_list = ["sg_persist --no-inquiry -v --out --register-ignore --param-sark 123aaa %s && sleep 1" % cmd_o,
                   "sg_persist --no-inquiry --in -k  %s && sleep 1" % cmd_o,
                   "sg_persist --no-inquiry -v --out --reserve --param-rk 123aaa --prout-type 5 %s && sleep 1" % cmd_o,
                   "sg_persist --no-inquiry --in -r %s && sleep 1" % cmd_o,
                   "sg_persist --no-inquiry -v --out --release --param-rk 123aaa --prout-type 5 %s && sleep 1" % cmd_o,
                   "sg_persist --no-inquiry --in -r %s && sleep 1" % cmd_o,
                   "sg_persist --no-inquiry -v --out --register --param-rk 123aaa --prout-type 5 %s && sleep 1" % cmd_o,
                   "sg_persist --no-inquiry --in -k %s && sleep 1" % cmd_o]
    for sg_cmd in sg_cmd_list:
        session.cmd_status_output(sg_cmd)
    new_qr_pid = process.run("pidof qemu-pr-helper",
                             ignore_status=True, shell=True).stdout_text.strip()
    if new_qr_pid is None:
        test.fail("qemu-pr-helper is not restarted after issuing pr commands to VM")


def test_coldplug_scsi_hostdev_duplicated_addresses_generate(test, params, env):
    """
    Test coldplug scsi hostdev with specific address, and then add one more scsi disk

    :param test: one test object instance
    :param params: dict wrapped with params
    :param env: environment instance
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    block_device = setup_iscsi_block_device()
    hostdev_xml = create_host_dev(params, block_device)
    addr_dict = {'controller': '0', 'bus': '0', 'target': '0', 'unit': '0'}

    # Specify address for host device
    new_one = hostdev_xml.Address(type_name='drive')
    for key, value in list({"attrs": addr_dict}.items()):
        setattr(new_one, key, value)
    hostdev_xml.address = new_one

    LOG.info("disk xml is: %s", hostdev_xml)
    xml_dump = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    xml_dump.add_device(hostdev_xml)
    xml_dump.sync()
    # add one more scsi disk, failure due to conflicts with SCSI host device address
    add_one_disk(params)


def run(test, params, env):
    """
    Test manipulate scsi device.

    1.Prepare test environment.
    2.Perform attach scsi hostdev device
    3.Recover test environment.
    4.Confirm the test result.
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    vm_names = params.get("vms").split()
    if len(vm_names) < 2:
        test.cancel("No multi vms provided.")

    # Backup vm xml files.
    vms_backup = []
    # it need use 2 VMs for testing.
    for i in list(range(2)):
        if virsh.is_alive(vm_name[i]):
            virsh.destroy(vm_name[i], gracefully=False)
        vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_names[i])
        vms_backup.append(vmxml_backup)

    coldplug = "yes" == params.get("coldplug")
    define_error = "yes" == params.get("define_error", "no")

    plug_mode = params.get("plug_mode")
    scsi_type = params.get("scsi_type")
    test_scenario = params.get("test_scenario")
    run_test_case = eval("test_%s_%s_%s" % (plug_mode, scsi_type, test_scenario))

    # avoid wrong configuration in later testing
    params.pop('boot_order')

    try:
        run_test_case(test, params, env)
        if coldplug:
            vm.start()
            vm.wait_for_login().close()
    except virt_vm.VMStartError as e:
        test.fail("VM failed to start."
                  "Error: %s" % str(e))
    except xcepts.LibvirtXMLError as xml_error:
        if not define_error:
            test.fail("Failed to define VM:\n%s" % str(xml_error))
        else:
            if params.get('error_msg') not in str(xml_error):
                test.fail("Get unexpected error message from: %s" % str(xml_error))
    else:
        if test_scenario == "alias":
            libvirt.check_qemu_cmd_line(params.get("alias_name"))
        elif test_scenario == "boot_order":
            check_scsi_device_boot_order(test, params, env)
        elif test_scenario == "same_hostdev_address":
            check_hostdev_xml(test, params, env)
        elif test_scenario == "tap_library":
            check_tap_library_device(test, params, env)
        elif test_scenario == "vdisk_hostdev_without_address":
            check_vdisk_hostdev_address_unit(test, params, env)
        elif test_scenario == "unplug_scsi_controller":
            check_scsi_controller(test, params, env)
        elif test_scenario == "shared_by_two_guests":
            check_hostdev_shareable_attr(test, params)
        elif test_scenario == "qemu_pr_helper":
            check_qemu_pr_helper(test, params, env)
    finally:
        # Recover VMs.
        for i in list(range(2)):
            if virsh.is_alive(vm_name[i]):
                virsh.destroy(vm_name[i], gracefully=False)
        LOG.info("Restoring vms...")
        for vmxml_backup in vms_backup:
            vmxml_backup.sync()
        # Delete the tmp files.
        libvirt.setup_or_cleanup_iscsi(is_setup=False)
        if test_scenario in ["boot_order", "same_hostdev_address", "tap_library", "qemu_pr_helper",
                             "duplicated_addresses_generate"]:
            try:
                libvirt.delete_scsi_disk()
            except Exception as e:
                LOG.info('ignore error in deleting scsi')
        for vm_name in cleanup_vms:
            if virsh.domain_exists(vm_name):
                if virsh.is_alive(vm_name):
                    virsh.destroy(vm_name)
                virsh.undefine(vm_name, "--nvram")
        # Clean up images
        for file_path in cleanup_files:
            if os.path.exists(file_path):
                os.remove(file_path)
