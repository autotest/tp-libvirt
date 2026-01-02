import ast
import logging
import platform
import shutil
import time

from avocado.utils import process

from virttest import utils_misc
from virttest import virsh
from virttest import virt_vm
from virttest import libvirt_version
from virttest import utils_disk

from virttest.libvirt_xml import vm_xml, xcepts
from virttest.utils_libvirt import libvirt_disk
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.virtual_disk import disk_base

LOG = logging.getLogger('avocado.' + __name__)


def get_new_disks(vm, old_partitions):
    """
    Get new virtual disks in VM after disk plug.

    :param vm: Virtual machine object
    :param old_partitions: already existing partitions in VM
    :return: New disks/partitions in VM
    """
    session = None
    try:
        session = vm.wait_for_login()
        if platform.platform().count('ppc64'):
            time.sleep(10)
        added_partitions = utils_disk.get_added_parts(session, old_partitions)
        LOG.debug("Newly added partition(s) is: %s", added_partitions)
        return added_partitions
    except Exception as err:
        raise Exception("Error happens when get new disk: %s" % str(err))
    finally:
        if session:
            session.close()


def get_usable_nvme_pci_address(test):
    """
    Get usable nvme device pci address

    :param test: Test object
    :return: usable nvme pci address in Dict
    """
    lspci_output = process.run(
        "lspci | grep -i 'Non-Volatile memory controller'",
        timeout=10, ignore_status=True, verbose=False, shell=True)
    # Expected output look something like below:
    # 17:00.0 Non-Volatile memory controller
    if lspci_output.exit_status != 0:
        test.cancel("Failed to execute lspci on host with output:{}".format(lspci_output))
    pci_address = lspci_output.stdout_text.strip().split()[0].split(":")
    address_dict = {}
    address_dict.update({'domain': '0x0000'})
    address_dict.update({'bus': '0x{}'.format(pci_address[0])})
    address_dict.update({'slot': '0x{}'.format(pci_address[1].split('.')[0])})
    address_dict.update({'function': '0x{}'.format(pci_address[1].split('.')[1])})
    return address_dict


def create_customized_disk(params, pci_address):
    """
    Create one customized disk with related attributes

    :param params: dict wrapped with params
    :param pci_address: pci address in dict format
    """
    type_name = params.get("type_name")
    disk_device = params.get("device_type")
    device_target = params.get("target_dev")
    device_bus = params.get("target_bus")
    device_format = params.get("driver_type")

    customized_disk = libvirt_disk.create_primitive_disk_xml(
        type_name, disk_device,
        device_target, device_bus,
        device_format, None, None)

    source_dict = ast.literal_eval(params.get("source_attrs", "{}"))
    disk_src_dict = {"attrs": source_dict}
    disk_source = customized_disk.new_disk_source(**disk_src_dict)
    disk_source.address = pci_address
    customized_disk.source = disk_source
    LOG.debug("create customized xml: %s", customized_disk)
    return customized_disk


def create_block_nvme_disk(test, params, disk_type, disk_obj, disk_dict):
    """
    Create a block NVME disk by partitioning an NVME device and creating disk XML

    :param test: Test object
    :param params: Dictionary containing test parameters
    :param disk_type: the disk type
    :param disk_obj: DiskBase object used for preparing disk XML configuration
    :param disk_dict: Dictionary containing disk configuration parameters
    :return: Tuple containing:
     disk_xml: Disk XML object for the created NVME partition
     nvme_device: String path of the base NVME device (e.g., "/dev/nvme0n1")
     nvme_partition_path: String path of the created partition (e.g., "/dev/nvme0n1p1")
     uuid: UUID of the LUKS secret (None if LUKS not used)
    """
    luks_secret_passwd = params.get("luks_secret_passwd", "redhat")
    with_luks = "yes" == params.get("with_luks", "no")
    luks_sec_uuid = None
    try:
        LOG.info("SETUP_TEST: Getting available NVME device in host")
        lsblk_result = process.run("lsblk -d -n -o NAME | grep nvme",
                                   timeout=10, ignore_status=True, shell=True)
        if lsblk_result.exit_status != 0:
            test.cancel("No NVME device found in host")

        nvme_devices = lsblk_result.stdout_text.strip().split('\n')
        for device in nvme_devices:
            device_path = "/dev/" + device.strip()
            mount_check = process.run(
                f"lsblk -n -o MOUNTPOINT {device_path} | grep -qE '\\S'",
                ignore_status=True, shell=True)
            if mount_check.exit_status != 0:
                nvme_device = device_path
                break
        else:
            test.cancel("No available unmounted NVME device found")

        LOG.info("SETUP_TEST: Creating GPT partition table on NVME device")
        result = process.run("parted -s %s mklabel gpt" % nvme_device,
                             timeout=30, ignore_status=False, verbose=True, shell=True)
        LOG.debug("GPT partition table creation result: %s", result)

        LOG.info("SETUP_TEST: Creating 1G primary partition on NVME device")
        result = process.run("parted -s %s mkpart primary 0%% 1G" % nvme_device,
                             timeout=30, ignore_status=False, verbose=True, shell=True)
        LOG.debug("Partition creation result: %s", result)
        time.sleep(2)

        # Use the created partition as disk image
        nvme_partition_path = nvme_device + "p1"
        luks_sec_dict = ast.literal_eval(params.get("luks_sec_dict") % nvme_partition_path)
        if with_luks:
            luks_sec_uuid = libvirt.create_secret(params)
            ret = virsh.secret_set_value(luks_sec_uuid, luks_secret_passwd,
                                         encode=True, debug=True)
            libvirt.check_exit_status(ret)

        nvme_disk_dict = disk_dict | luks_sec_dict
        disk_xml = libvirt_vmxml.create_vm_device_by_type("disk", nvme_disk_dict)
        LOG.debug("Created disk XML: %s", disk_xml)
        return disk_xml, nvme_device, nvme_partition_path, luks_sec_uuid

    except Exception as err:
        test.fail("Failed to create block NVME disk: %s" % str(err))


def run_attach_detach_test(test, params, vm, vmxml, vm_name, hotplug):
    """
    Run original attach/detach test with nvme device

    :param test: Test object
    :param params: Test paramters
    :param vm: VM instance
    :param vmxml: VM xml
    :param vm_name: VM name
    :param hotplug: Confirm if we need to hotplug
    """
    if vm.is_dead():
        vm.start()
    session = vm.wait_for_login()
    old_partitions = utils_disk.get_parts_list(session)
    session.close()
    vm.destroy(gracefully=False)

    pci_addr_in_dict = get_usable_nvme_pci_address(test)

    # Delete partitions if exist on nvme device
    process.run("sfdisk --delete  /dev/nvme[0-9]n[0-9]",
                timeout=10, ignore_status=True, verbose=True, shell=True)

    device_obj = create_customized_disk(params, pci_addr_in_dict)
    if not hotplug:
        vmxml.add_device(device_obj)
        vmxml.sync()
    vm.start()
    vm.wait_for_login().close()
    if hotplug:
        virsh.attach_device(vm_name, device_obj.xml, flagstr="--live",
                            ignore_status=False, debug=True)

    utils_misc.wait_for(lambda: get_new_disks(vm, old_partitions), 20)
    new_disks = get_new_disks(vm, old_partitions)
    if len(new_disks) != 1:
        test.fail("Attached 1 virtual disk but got %s." % len(new_disks))
    new_disk = new_disks[0]
    if platform.platform().count('ppc64'):
        time.sleep(10)
    if not libvirt_disk.check_virtual_disk_io(vm, new_disk):
        test.fail("Cannot operate the newly added disk in vm.")

    virsh.detach_device(vm_name, device_obj.xml, flagstr="--live",
                        debug=True, ignore_status=False)


def verify_blockcopy_result(test, params, vm_name, original_source, nvme_dest_disk):
    """
    Verify blockcopy result by checking VM XML

    :param test: Test object
    :param params: Dictionary containing test parameters
    :param vm_name: Virtual machine name
    :param original_source: Original disk source path before blockcopy operation
    :param nvme_dest_disk: NVME destination disk XML object used in blockcopy
    """
    specific_option = params.get("blockcopy_option", "")
    target_dev = params.get("target_dev", "vdb")
    disk_type = params.get("disk_type", "file")
    nvme_disk_type = params.get("nvme_disk_type", "block")
    current_vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    LOG.debug("The current guest xml after blockcopy is: %s" % current_vmxml)
    disk_sources = current_vmxml.get_disk_source(vm_name)
    disk_obj = disk_base.DiskBase(test, None, params)
    if len(disk_sources) < 2:
        test.fail("VM doesn't have enough disks after blockcopy operation.")

    if "--finish" in specific_option:
        current_disk_source = disk_obj.get_source_list(current_vmxml, disk_type, target_dev)[0]
        if current_disk_source != original_source:
            test.fail("Expected VM to keep old disk source '%s', but found '%s'" %
                      (original_source, current_disk_source))
        LOG.info("PASS: VM disk kept the old disk source as expected with --finish")

    elif "--pivot" in specific_option:
        current_disk_source = disk_obj.get_source_list(current_vmxml, nvme_disk_type, target_dev)[0]
        if current_disk_source != nvme_dest_disk:
            test.fail("Expected VM to pivot to nvme destination, but disk source remains unchanged: '%s'" %
                      current_disk_source)
        LOG.info("PASS: VM disk pivoted to the new nvme destination as expected with --pivot")


def run_blockcopy_test(test, params, vm, vm_name):
    """
    Run blockcopy test with nvme destination

    :param test: Test object
    :param params: Test parameters
    :param vm: VM instance
    :param vm_name: VM name
    """
    libvirt_version.is_libvirt_feature_supported(params)
    target_dev = params.get("target_dev", "vdb")
    disk_type = params.get("disk_type", "file")
    nvme_disk_type = params.get("nvme_disk_type", "block")
    disk_dict = ast.literal_eval(params.get("disk_dict") % disk_type)
    nvme_disk_dict = ast.literal_eval(params.get("disk_dict") % nvme_disk_type)
    common_options = params.get("blockcopy_common_options")
    specific_option = params.get("blockcopy_option", "")
    with_luks = "yes" == params.get("with_luks", "no")
    luks_extra_parameter = params.get("luks_extra_parameter", "")
    disk_obj = disk_base.DiskBase(test, vm, params)

    LOG.info("TEST_STEP1: Start VM.")
    nvme_disk, nvme_device, nvme_partition_path, luks_sec_uuid = create_block_nvme_disk(test, params,
                                                                                        nvme_disk_type,
                                                                                        disk_obj,
                                                                                        nvme_disk_dict)
    disk_info_dict = utils_misc.get_image_info(nvme_partition_path)
    disk_size = disk_info_dict.get("vsize")
    disk_size_bytes = str(disk_size) + "b"
    original_disk_source = disk_obj.add_vm_disk(disk_type, disk_dict, size=disk_size_bytes)
    LOG.debug("Original disk source: %s", original_disk_source)
    if vm.is_dead():
        vm.start()
    LOG.debug("The current guest xml is: %s" % (virsh.dumpxml(vm_name).stdout_text))
    vm.wait_for_login().close()

    LOG.info("TEST_STEP2: Execute blockcopy command with option '%s'.", specific_option)
    if with_luks:
        cmd = ("qemu-img create -f qcow2 %s %s %s" % (luks_extra_parameter,
                                                      nvme_partition_path,
                                                      disk_size_bytes))
        process.run(cmd, shell=True, ignore_status=False)
    options = "--xml %s %s %s" % (nvme_disk.xml, specific_option, common_options)
    virsh.blockcopy(vm_name, target_dev, options, debug=True, ignore_status=False)
    LOG.debug("Blockcopy completed successfully.")

    LOG.info("TEST_STEP3: Verify blockcopy result in VM XML.")
    verify_blockcopy_result(test, params, vm_name, original_disk_source, nvme_partition_path)

    if vm.is_alive():
        vm.destroy(gracefully=False)
    process.run("wipefs -a %s" % nvme_device, shell=True, ignore_status=False)
    disk_obj.cleanup_disk_preparation(disk_type)
    if with_luks:
        virsh.secret_undefine(luks_sec_uuid, debug=True, ignore_status=False)


def run(test, params, env):
    """
    Test nvme virtual.

    For attach test case:
    1.Prepare a vm with nvme type disk
    2.Attach the virtual disk to the vm
    3.Start vm
    4.Check the disk in vm
    5.Detach nvme device

    For blockcopy test case:
    1.Start a VM with a regular disk
    2.Prepare nvme destination disk XML
    3.Execute blockcopy with --xml option (--finish or --pivot)
    4.Check VM XML after operation
    5.Verify disk source in VM
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    with_blockcopy = "yes" == params.get("with_blockcopy", "no")
    with_attach_detach = "yes" == params.get("with_attach_detach", "no")
    hotplug = "yes" == params.get("virt_device_hotplug")
    pkgs_host = params.get("pkgs_host", "")

    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    vmxml = vmxml_backup.copy()

    try:
        # install essential package in host
        if not shutil.which('lspci'):
            test.error("package {} not installed, please install them before testing".format(pkgs_host))

        if with_blockcopy:
            run_blockcopy_test(test, params, vm, vm_name)
        elif with_attach_detach:
            run_attach_detach_test(test, params, vm, vmxml, vm_name, hotplug)
        else:
            test.cancel("No test type specified, neither blockcopy nor attach/detach.")

    except virt_vm.VMStartError as details:
        test.fail("VM failed to start. Error: %s" % str(details))
    except xcepts.LibvirtXMLError as xml_error:
        test.fail("VM failed to define. Error: %s" % str(xml_error))
    finally:
        if vm.is_alive():
            vm.destroy(gracefully=False)
        # Restoring vm
        vmxml_backup.sync()
