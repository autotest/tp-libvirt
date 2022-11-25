import os
import logging

import aexpect

from avocado.utils import process
from avocado.utils import linux_modules

from virttest import libvirt_xml

from virttest import virt_vm, utils_misc
from virttest import virsh

from virttest.libvirt_xml import vm_xml

from virttest.utils_libvirt import libvirt_disk
from virttest.utils_test import libvirt

LOG = logging.getLogger('avocado.' + __name__)

cleanup_files = []


def create_customized_disk(params):
    """
    Create one customized disk with related attributes

    :param params: dict wrapped with params
    """
    disk_device = params.get("device_type")
    device_bus = params.get("target_bus")
    device_target = params.get("target_dev")
    device_format = params.get("target_format")
    type_name = params.get("type_name")
    source_file_path = params.get("virt_disk_device_source")
    if source_file_path:
        # It need create small size qcow2 disk,which is not supported from libvirt.create_local_disk
        process.run("qemu-img create -f %s %s 1M" % (device_format, source_file_path),
                    ignore_status=True, shell=True)
        cleanup_files.append(source_file_path)
    source_dict = {}
    source_dict.update({"file": source_file_path})
    disk_src_dict = {"attrs": source_dict}

    customized_disk = libvirt_disk.create_primitive_disk_xml(
        type_name, disk_device,
        device_target, device_bus,
        device_format, disk_src_dict, None)
    LOG.debug("create customized xml: %s", customized_disk)
    return customized_disk


def unload_scsi_debug():
    """
    Unload scsi_debug module gracefully
    """
    def _unload():
        """ unload scsi_debug module """
        linux_modules.unload_module("scsi_debug")
        return True
    utils_misc.wait_for(_unload, timeout=20, ignore_errors=True)


def get_emulated_block_device():
    """
    get one emulated block device provided by scsi_debug

    :return: return one block device, e.g sdb
    """
    # If scsi_debug is loaded already, unload it first
    if linux_modules.module_is_loaded("scsi_debug"):
        unload_scsi_debug()

    # Load module and get scsi disk name
    utils_misc.wait_for(lambda: linux_modules.load_module("scsi_debug lbpu=1 lbpws=1"), timeout=10, ignore_errors=True)
    scsi_disk = process.run("lsscsi|grep scsi_debug|"
                            "awk '{print $6}'", ignore_status=True, shell=True).stdout_text.strip()
    return scsi_disk


def create_two_partition_on_block_device(block_device):
    """
    Create two partition on scsi_debug block device

    :param block_device: block device
    """
    mk_label_cmd = "parted %s mklabel gpt -s"
    mk_partition_1_cmd = "parted %s mkpart p 1M 2M"
    mk_partition_2_cmd = "parted %s mkpart p 3M 4M"
    # Create two partitions on block device
    for cmd in [mk_label_cmd, mk_partition_1_cmd, mk_partition_2_cmd]:
        process.run(cmd % block_device,
                    ignore_status=True, shell=True)


def create_snapshot_xml(target_dev, block_device):
    """
    Create one snapshot disk with related attributes

    :param target_dev: target device,e.g vda
    :param block_device: block device
    :return return one snap_xml
    """
    snap_xml = libvirt_xml.SnapshotXML()
    new_disks = []
    disk_xml = snap_xml.SnapDiskXML()
    disk_xml.disk_name = target_dev
    disk_xml.source = disk_xml.new_disk_source(**{"attrs": {"dev": "%s1" % block_device}})
    new_disks.append(disk_xml)

    # update system disk with snapshot=no
    vda_disk_xml = snap_xml.SnapDiskXML()
    vda_disk_xml.disk_name = 'vda'
    vda_disk_xml.snapshot = 'no'
    new_disks.append(vda_disk_xml)

    snap_xml.set_disks(new_disks)
    LOG.debug("create_snapshot xml: %s", snap_xml)
    return snap_xml


def perform_block_opeations(params, vm, block_device):
    """
    Perform block commit/pull/copy operations on block device

    :param params: parameters wrapped in one dictionary
    :param vm: VM instance
    :param block_device: block device
    :return return one snap_xml
    """
    # It needs constantly execute virsh domblkinfo in the background
    host_session = aexpect.ShellSession("sh")
    target_dev = params.get("target_dev")
    domblkinfo_cmd = "for i in {1..100}; do virsh domblkinfo %s %s; sleep 2; \
        done" % (vm.name, target_dev)
    LOG.info("Sending '%s' to  host ", domblkinfo_cmd)
    host_session.sendline(domblkinfo_cmd)

    snapshot = create_snapshot_xml(target_dev, block_device)
    virsh.snapshot_create(vm.name, "%s  --disk-only --no-metadata" % snapshot.xml,
                          debug=True, ignore_status=False)
    vm.wait_for_login().close()
    virsh.blockcommit(vm.name, target_dev, params.get('block_commit_option'),
                      debug=True, ignore_status=False)
    vm.wait_for_login().close()
    virsh.blockpull(vm.name, target_dev, params.get('block_pull_option'),
                    debug=True, ignore_status=False)
    vm.wait_for_login().close()

    # Undefine Vm to make it be transient Vm
    vm.destroy(gracefully=False)
    transient_vmxml = vm_xml.VMXML.new_from_dumpxml(vm.name)
    virsh.undefine(vm.name, options='--nvram', debug=True, ignore_status=False)
    virsh.create(transient_vmxml.xml, ignore_status=False, debug=True)

    virsh.blockcopy(vm.name, target_dev, "%s2" % block_device, params.get('block_copy_option'),
                    debug=True, ignore_status=False)


def check_qemudomaingetblockinfo_error(vm, test):
    """
    Check related error message in libvirtd/qemu log output

    :param vm: VM instance
    :param test: test object
    """
    str_to_grep = "error : qemuDomainGetBlockInfo"
    libvirtd_log_file = os.path.join(test.debugdir, "libvirtd.log")
    qemu_log_file = os.path.join('/var/log/libvirt/qemu/', "%s.log" % vm.name)
    for log_file in [libvirtd_log_file, qemu_log_file]:
        if not libvirt.check_logfile(str_to_grep, log_file, str_in_log=False):
            test.fail('Find unexpected error:%s in log file:%s'
                      % (str_to_grep, log_file))


def run(test, params, env):
    """
    Test virDomainGetBlockInfo shouldn't get error while doing block operations on block device.
    And it is related to https://bugzilla.redhat.com/show_bug.cgi?id=1452045

    1.Prepare test environment,destroy or suspend a VM.
    2.Prepare test xml.
    3.Perform test operation.
    4.Recover test environment.
    5.Confirm the test result.
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    hotplug = "yes" == params.get("virt_device_hotplug")
    status_error = "yes" == params.get("status_error")

    # Back up xml file
    if vm.is_alive():
        vm.destroy(gracefully=False)
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    try:
        vmxml = vmxml_backup.copy()
        # Start VM with additional disk
        device_obj = create_customized_disk(params)
        if not hotplug:
            vmxml.add_device(device_obj)
            vmxml.sync()
        vm.start()
        vm.wait_for_login().close()
        blk_device = get_emulated_block_device()
        create_two_partition_on_block_device(blk_device)
        perform_block_opeations(params, vm, blk_device)
    except virt_vm.VMStartError as e:
        if status_error:
            LOG.debug("VM failed to start as expected."
                      "Error: %s", str(e))
        else:
            test.fail("VM failed to start."
                      "Error: %s" % str(e))
    else:
        check_qemudomaingetblockinfo_error(vm, test)
    finally:
        libvirt.clean_up_snapshots(vm_name, domxml=vmxml_backup)
        # Recover VM
        LOG.info("Restoring vm...")
        if vm.is_alive():
            vm.destroy(gracefully=False)
        vmxml_backup.sync()
        # Clean up images
        for file_path in cleanup_files:
            if os.path.exists(file_path):
                os.remove(file_path)
        # Unload scsi_debug module if loaded
        unload_scsi_debug()
