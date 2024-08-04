import re
import platform

from avocado.core import exceptions
from avocado.utils import cpu
from avocado.utils import memory as avocado_mem

from virttest import virsh
from virttest import libvirt_version
from virttest import utils_misc
from virttest.libvirt_xml.devices import memory
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.utils_version import VersionInterval
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_libvirt import libvirt_misc

virsh_dargs = {"ignore_status": False, "debug": True}


def convert_data_size(current_size, dest_unit="KiB"):
    """
    Convert source value to expected value
    :param current_size: current size str, eg: 1024MB
    :param dest_unit: dest size unit, eg: KiB, MiB
    :return: dest_size: The size is converted, eg: 1(the dest unit is given,
    so it means 1GB)
    """
    decimal = ['B', 'KB', 'MB', 'GB', 'TB', 'PB', 'EB', 'ZB', 'YB']
    binary = ['B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB', 'EiB', 'ZiB', 'YiB']
    current_value = re.findall(r"\d+", current_size)[0]
    current_unit = re.findall(r"\D+", current_size)[0]

    if current_unit == "bytes":
        bytes_size = int(current_value)
    else:
        factor, unit_list = 0, []
        if current_unit in decimal:
            factor = 1000
            unit_list = decimal
        elif current_unit in binary:
            factor = 1024
            unit_list = binary
        else:
            raise exceptions.TestError("The unit:%s you input is not "
                                       "found" % current_unit)
        bytes_size = int(current_value) * (
                    factor ** (unit_list.index(current_unit)))

    if dest_unit in decimal:
        factor = 1000
        unit_list = decimal
    elif dest_unit in binary:
        factor = 1024
        unit_list = binary
    else:
        raise exceptions.TestError(
            "The unit:%s you input is not found" % current_unit)

    dest_size = bytes_size / (factor ** (unit_list.index(dest_unit)))
    if isinstance(dest_size, float):
        return dest_size
    return int(dest_size)


def check_supported_version(params, test, vm):
    """
    Check the supported version

    :param params: Dictionary with the test parameters
    :param test: Test object
    :param vm: Vm object
    """
    guest_required_kernel = params.get('guest_required_kernel')
    libvirt_version.is_libvirt_feature_supported(params)
    utils_misc.is_qemu_function_supported(params)
    if not guest_required_kernel:
        return

    if not vm.is_alive():
        vm.start()
    vm_session = vm.wait_for_login()
    vm_kerv = vm_session.cmd_output('uname -r').strip().split('-')[0]
    vm_session.close()
    if vm_kerv not in VersionInterval(guest_required_kernel):
        test.cancel("Got guest kernel version:%s, which is not in %s" %
                    (vm_kerv, guest_required_kernel))


def prepare_mem_obj(dest_dict):
    """
    Prepare memory object
    :param dest_dict: dimm memory dict.
    :return mem_obj, memory object.
    """
    mem_obj = memory.Memory()
    mem_obj.setup_attrs(**dest_dict)

    return mem_obj


def create_file_within_nvdimm_disk(test, vm_session, test_device, test_file,
                                   mount_point, error_msg="", test_str="test_text",
                                   block_size=4096):
    """
    Create a test file in the nvdimm file disk

    :param test: test object
    :param vm_session: VM session
    :param test_device: str, device value
    :param test_file: str, file name to be used
    :param mount_point: mount point path.
    :param error_msg: Error msg content when useing mkfs cmd
    :param test_str: str to be written into the nvdimm file disk
    :param block_size: int, block size for mkfs.xfs -b
    """
    # Create a file system
    bsize_str = '-b size={}'.format(block_size) if block_size != 0 else ''
    if any(platform.platform().find(ver) for ver in ('el8', 'el9')):
        cmd = 'mkfs.xfs -f {} {} -m reflink=0'.format(test_device, bsize_str)
    else:
        cmd = 'mkfs.xfs -f {} {}'.format(test_device, bsize_str)

    vm_session.cmd("mkdir -p %s" % mount_point)
    output = vm_session.cmd_output(cmd)
    if error_msg:
        if error_msg not in output:
            test.fail("Expect to get '%s' in '%s'" % (error_msg, output))
        else:
            return
    test.log.debug("Command '%s' output:%s", cmd, output)

    # Mount the file system
    uuid = re.findall(
        r' UUID="(\S+)"', vm_session.cmd_output('blkid %s' % test_device))[0]
    vm_session.cmd_status_output('mount -o dax -U {} {}'.format(uuid, mount_point))

    cmd = 'echo \"%s\" >%s' % (test_str, test_file)
    vm_session.cmd(cmd)
    vm_session.cmd_output('umount %s' % mount_point)


def adjust_memory_size(params):
    """
    Adjust the memory device size for different arch hugepage size

    :param params: a dict for parameters
    eg: In arm, we need to consider:
       2M on 4k kernel package.
       512M on 64k kernel package.
    """
    default_pagesize_KiB = avocado_mem.get_huge_page_size()

    if cpu.get_arch().startswith("aarch"):
        params.update({'block_size': default_pagesize_KiB})
        params.update({'request_size': default_pagesize_KiB})
        params.update({'target_size': default_pagesize_KiB*2})


def define_guest_with_memory_device(params, mem_attr_list, vm_attrs=None):
    """
    Define guest with specified memory device.

    :param params: a dict for parameters.
    :param mem_attr_list: memory device attributes list.
    :param vm_attrs: basic vm attributes to define.
    """
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(params.get("main_vm"))
    if vm_attrs:
        vmxml.setup_attrs(**vm_attrs)

    if not isinstance(mem_attr_list, list):
        mem_attr_list = [mem_attr_list]
    for mem in mem_attr_list:
        memory_object = libvirt_vmxml.create_vm_device_by_type('memory', mem)
        vmxml.devices = vmxml.devices.append(memory_object)
    vmxml.sync()


def plug_memory_and_check_result(test, params, mem_dict, operation='attach',
                                 expected_error='', expected_event='',
                                 alias='', **kwargs):
    """
    Hot plug or hot unplug memory and check event.

    :param test: test object.
    :param params: dictionary with the test parameters.
    :param mem_dict: the memory dict to plug.
    :param operation: the operation of plug or unplug.
    :param expected_error: expected error after plug or unplug.
    :param expected_event: expected event for plug or unplug.
    :param alias: the plugged device alias
    """
    vm_name = params.get('main_vm')
    plug_mem = libvirt_vmxml.create_vm_device_by_type('memory', mem_dict)

    wait_event = True if expected_event else False
    if operation == "attach":
        res = virsh.attach_device(vm_name, plug_mem.xml, wait_for_event=wait_event,
                                  event_type=expected_event, debug=True, **kwargs)
    elif operation == "detach":
        res = virsh.detach_device(vm_name, plug_mem.xml, wait_for_event=wait_event,
                                  event_type=expected_event, debug=True, **kwargs)
    elif operation == "detach_alias":
        res = virsh.detach_device_alias(
            vm_name, alias=alias, wait_for_event=wait_event,
            event_type=expected_event, debug=True, **kwargs)

    if expected_error:
        libvirt.check_result(res, expected_fails=expected_error)
    else:
        libvirt.check_exit_status(res)


def check_dominfo(vm, test, expected_max, expected_used):
    """
    Check Max memory value and Used memory in virsh dominfo result.

    :param vm: vm object.
    :param test: test object.
    :param params: dictionary with the test parameters.
    :param expected_max: expected Max memory in virsh dominfo.
    :param expected_used: expected Used memory in virsh dominfo.
    """
    result = virsh.dominfo(vm.name, **virsh_dargs).stdout_text.strip()

    dominfo_dict = libvirt_misc.convert_to_dict(
        result, pattern=r'(\S+ \S+):\s+(\S+)')
    if dominfo_dict["Max memory"] != expected_max:
        test.fail("Memory value should be %s " % expected_max)
    if dominfo_dict["Used memory"] != expected_used:
        test.fail("Current memory should be %s " % expected_used)
    test.log.debug("Check virsh dominfo successfully.")


def check_mem_page_sizes(test, pg_size=None, hp_size=None, hp_list=None):
    """
    Check host is suitable for various memory page sizes

    :param test: test object
    :param pg_size: int, default memory page size in KiB unit
    :param hp_size: int, default memory huge page size in KiB unit
    :param hp_list: list, huge page size int list in KiB unit
    """
    default_page_size = avocado_mem.get_page_size() / 1024
    if pg_size and pg_size != default_page_size:
        test.cancel("Expected host default page size is %s KiB, but get %s KiB" %
                    (pg_size, default_page_size))
    default_huge_page_size = avocado_mem.get_huge_page_size()
    if hp_size and hp_size != default_huge_page_size:
        test.cancel("Expected host default huge page size is %s KiB, but get %s KiB" %
                    (hp_size, default_huge_page_size))
    supported_hp_size_list = avocado_mem.get_supported_huge_pages_size()
    if hp_list and not set(hp_list).issubset(set(supported_hp_size_list)):
        test.cancel("Expected huge page size list is %s, but get %s" %
                    (hp_list, supported_hp_size_list))


def compare_values(test, expected, actual, check_item=''):
    """
    Compare two values are the same.

    :params test, test object.
    :params expected, expected value.
    :params actual, actual value.
    :params check_item, the item to be checked.
    """
    if expected != actual:
        test.fail("Expect %s to get '%s' instead of '%s' " % (
            check_item, expected, actual))
    else:
        test.log.debug("Check %s %s PASS", check_item, actual)
