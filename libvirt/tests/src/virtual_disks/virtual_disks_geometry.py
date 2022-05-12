import ast
import logging
import os

from avocado.utils import process

from virttest import virsh
from virttest import virt_vm

from virttest.libvirt_xml import vm_xml, xcepts

from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_disk
from virttest.utils_libvirt import libvirt_misc

LOG = logging.getLogger('avocado.' + __name__)
cleanup_files = []


def create_customized_disk(params):
    """
    Create one customized disk with related attributes

    :param params: dict wrapped with params
    :return: disk object
    """
    type_name = params.get("type_name")
    disk_device = params.get("device_type")
    device_target = params.get("target_dev")
    device_bus = params.get("target_bus")
    device_format = params.get("target_format")
    source_file_path = "%s.%s" % (params.get("virt_disk_device_source"), device_bus)
    source_dict = {}

    if source_file_path:
        libvirt.create_local_disk("file", source_file_path, 1,
                                  disk_format=device_format)
        cleanup_files.append(source_file_path)
        if 'block' in type_name:
            source_dict.update({"dev": source_file_path})
        else:
            source_dict.update({"file": source_file_path})

    disk_src_dict = {"attrs": source_dict}

    customized_disk = libvirt_disk.create_primitive_disk_xml(
        type_name, disk_device,
        device_target, device_bus,
        device_format, disk_src_dict, None)

    # Set geometry attributes in disk
    chs_geometry_attrs = params.get('chs_attrs')
    if chs_geometry_attrs:
        customized_disk.geometry = eval(chs_geometry_attrs)

    detect_zeroes = params.get("detect_zeroes")
    if detect_zeroes:
        customized_disk.driver = dict(customized_disk.driver, **{'detect_zeroes': detect_zeroes})

    discard = params.get("discard")
    if discard:
        customized_disk.driver = dict(customized_disk.driver, **{'discard': discard})

    iotune_attrs = params.get("iotune_attrs")
    if iotune_attrs:
        iotune_instance = customized_disk.new_iotune(**eval(iotune_attrs))
        customized_disk.iotune = iotune_instance

    LOG.debug("create customized xml: %s", customized_disk)
    return customized_disk


def check_chs_values(params, vm_name, test):
    """
    Check cylinders, heads, sectors value from qemu line since there are no other ways to check this.

    :params params: wrapped dict with all parameters
    :param vm_name: VM name
    :param test: test assert object
    """
    chs_geometry_attrs_dict = eval(params.get('chs_attrs'))
    cmd = ("ps -ef | grep %s | grep -v grep " % vm_name)
    process.system_output(cmd, shell=True, verbose=True)
    cmd += " | grep -c -E cyls.*%s.*heads.*%s.*secs.*%s" % (chs_geometry_attrs_dict['cyls'],
                                                            chs_geometry_attrs_dict['heads'],
                                                            chs_geometry_attrs_dict['secs'])
    if process.system(cmd, ignore_status=True, shell=True):
        test.fail("Check disk chs geometry option failed with %s" % cmd)


def check_discard_detect_zeroes_values(params, vm_name, test):
    """
    Check detect_zeroes and discard value from qemu line since there are no other ways to check this.

    :params params: wrapped dict with all parameters
    :param vm_name: VM name
    :param test: test assert object
    """
    cmd = ("ps -ef | grep %s | grep -v grep " % vm_name)
    process.system_output(cmd, shell=True, verbose=True)
    detect_zeroes = params.get('detect_zeroes')
    if detect_zeroes:
        cmd += " | grep .*detect-zeroes.*%s.*" % (detect_zeroes)
    discard = params.get('discard')
    if discard:
        cmd += " | grep .*discard.*%s.*" % (discard)
    if process.system(cmd, ignore_status=True, shell=True):
        test.fail("Check disk geometry option failed with %s" % cmd)


def check_log_file(params, vm_name, test):
    """
    Check detect_zeroes and discard value from libvirtd log.

    :params params: wrapped dict with all parameters
    :param vm_name: VM name
    :param test: test assert object
    """
    str_to_grep = ""
    log_file = os.path.join(test.debugdir, "libvirtd.log")
    detect_zeroes = params.get('detect_zeroes')
    if detect_zeroes:
        str_to_grep = "detect-zeroes.*%s.*" % (detect_zeroes)
        if not libvirt.check_logfile(str_to_grep, log_file):
            test.fail('Failed to check detect-zeroes as expected:%s in log file:%s' % (str_to_grep, log_file))
    discard = params.get('discard')
    if discard:
        str_to_grep = "discard.*%s.*" % (discard)
        if not libvirt.check_logfile(str_to_grep, log_file):
            test.fail('Failed to check discard as expected:%s in log file:%s' % (str_to_grep, log_file))


def check_iotune_values(params, vm_name, test):
    """
    Check read or write bytes and input and output per second from virsh command

    :params params: wrapped dict with all parameters
    :param vm_name: VM name
    :param test: test assert object
    """
    device_target = params.get("target_dev")
    block_tune_raw_output = virsh.blkdeviotune(vm_name, device_target, debug=True).stdout_text.strip()
    cmd_output_dict = libvirt_misc.convert_to_dict(block_tune_raw_output, r"^(\w+)\s*:\s+(\d+)")
    iotune_attrs = params.get('iotune_attrs')
    iotune_attrs_dict = eval(iotune_attrs)
    if not all(str(iotune_attrs_dict[key]) == cmd_output_dict.get(key) for key in iotune_attrs_dict):
        test.fail("command output: %s are not all equal with preset ones: %s"
                  % (",".join('{0}={1}'.format(k, v) for k, v in cmd_output_dict.items()), iotune_attrs))


def check_total_bytes_sec_values(params, vm_name, test):
    """
    Check total_bytes_sec

    :params params: wrapped dict with all parameters
    :param vm_name: VM name
    :param test: test assert object
    """
    device_target = params.get("target_dev")
    total_bytes_sec = params.get("total_bytes_sec")
    total_bytes_sec_dict = eval(params.get("total_bytes_sec"))
    for key in total_bytes_sec_dict:
        result = virsh.blkdeviotune(vm_name, device_target, " --total-bytes-sec %s" % key,
                                    ignore_status=True, debug=True)
        libvirt.check_exit_status(result, ast.literal_eval(total_bytes_sec_dict.get(key)))
        if not ast.literal_eval(total_bytes_sec_dict.get(key)):
            block_tune_raw_output = virsh.blkdeviotune(vm_name, device_target, debug=True).stdout_text.strip()
            cmd_output_dict = libvirt_misc.convert_to_dict(block_tune_raw_output, r"^(\w+)\s*:\s+(\d+)")
            if cmd_output_dict.get('total_bytes_sec') != key:
                test.fail("command output: %s is not all equal with preset one: %s" % (cmd_output_dict.get('total_bytes_sec'), key))


def run(test, params, env):
    """
    Test set cylinders, heads, sectors geometry attributes for disk types:scsi, sata, virtio, usb
    Test set detect_zeroes and discard for disk
    Test set read_bytes_sec/write_bytes_sec/total_bytes_sec together
    Test set max total_bytes_sec

    1.Prepare test environment,destroy or suspend a VM.
    2.Prepare test xml for different devices.
    3.Perform test operation.
    4.Recover test environment.
    5.Confirm the test result.
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    # Disk specific attributes.
    backend_device = params.get("backend_device", "disk")

    hotplug = "yes" == params.get("virt_device_hotplug")
    status_error = "yes" == params.get("status_error")
    define_error = "yes" == params.get("define_error", "no")
    error_msg = params.get("error_msg", "chs geometry can not be set")

    device_obj = None

    # Back up xml file.
    if vm.is_alive():
        vm.destroy(gracefully=False)
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    try:
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        device_obj = create_customized_disk(params)
        if not hotplug:
            vmxml.add_device(device_obj)
            vmxml.sync()
        vm.start()
        vm.wait_for_login().close()
        if hotplug:
            LOG.info("attaching devices, expecting error...")
            result = virsh.attach_device(vm_name, device_obj.xml, debug=True)
            libvirt.check_exit_status(result)
    except virt_vm.VMStartError as e:
        test.fail("VM failed to start."
                  "Error: %s" % str(e))
    except xcepts.LibvirtXMLError as xml_error:
        if not define_error:
            test.fail("Failed to define VM:\n%s" % str(xml_error))
        else:
            LOG.info("As expected, failed to define VM due to reason:\n%s", str(xml_error))
    except Exception as ex:
        test.fail("unexpected exception happen: %s" % str(ex))
    else:
        if backend_device in ["chs_scsi", "chs_virtio", "chs_sata"]:
            check_chs_values(params, vm_name, test)
        elif backend_device in ["detect_zeroes_on", "detect_zeroes_off"]:
            check_discard_detect_zeroes_values(params, vm_name, test)
        elif backend_device in ["discard_ignore_detect_zeroes_unmap",
                                "discard_unmap_detect_zeroes_unmap"]:
            # Regarding hotplug, it need check libvirtd log
            check_log_file(params, vm_name, test)
        elif backend_device in ["set_read_write_bytes_iops_sec_iotune"]:
            check_iotune_values(params, vm_name, test)
        elif backend_device in ["set_total_bytes_sec_boundary_iotune"]:
            check_total_bytes_sec_values(params, vm_name, test)
    finally:
        # Recover VM.
        if vm.is_alive():
            vm.destroy(gracefully=False)
        LOG.info("Restoring vm...")
        vmxml_backup.sync()
        # Clean up images
        for file_path in cleanup_files:
            if os.path.exists(file_path):
                os.remove(file_path)
