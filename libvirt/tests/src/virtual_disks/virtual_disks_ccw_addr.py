import logging as log
import os
import re

from avocado.utils import process

from virttest import virt_vm
from virttest import virsh
from virttest import utils_libvirtd
from virttest import utils_split_daemons

from virttest.libvirt_xml import vm_xml, xcepts

from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_disk


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def create_ccw_addr_controller(params):
    """
    Create one ccw address controller device

    :param params: dict wrapped with params
    """

    contr_dict = {'controller_type': 'scsi',
                  'controller_index': '10'}

    ccw_addr_controller = libvirt.create_controller_xml(contr_dict)

    addr_dict = eval(params.get("addr_attrs"))
    ccw_addr_controller.address = ccw_addr_controller.new_controller_address(
        **{"attrs": addr_dict})
    logging.debug("create_ccw_addr_controller xml: %s", ccw_addr_controller)
    return ccw_addr_controller


def create_ccw_addr_rng(params):
    """
    Create one ccw address rng device

    :param params: dict wrapped with params
    """
    rng = libvirt.create_rng_xml(params)
    addr_dict = eval(params.get("addr_attrs"))
    rng.address = rng.new_rng_address(
        **{"attrs": addr_dict})
    logging.debug("create_ccw_addr_rng xml: %s", rng)
    return rng


def create_ccw_addr_disk(params):
    """
    Create one ccw address disk

    :param params: dict wrapped with params
    """
    type_name = params.get("type_name")
    disk_device = params.get("device_type")
    device_target = params.get("target_dev")
    device_bus = params.get("target_bus")
    device_format = params.get("target_format")
    source_file_path = params.get("virt_disk_device_source")
    disk_src_dict = {"attrs": {"file": source_file_path}}
    addr_str = params.get("addr_attrs")

    if source_file_path:
        libvirt.create_local_disk("file", source_file_path, 1, device_format)
    ccw_addr_disk = libvirt_disk.create_primitive_disk_xml(
        type_name, disk_device,
        device_target, device_bus,
        device_format, disk_src_dict, None)
    if addr_str:
        addr_dict = eval(addr_str)
        ccw_addr_disk.address = ccw_addr_disk.new_disk_address(
            **{"attrs": addr_dict})
    logging.debug("create_ccw_addr_disk xml: %s", ccw_addr_disk)
    return ccw_addr_disk


def check_libvirtd_process_id(ori_pid_libvirtd, test):
    """
    Check libvirtd process id not change

    :param params: original libvirtd process id
    :param test: test assert object
    """
    if not utils_split_daemons.is_modular_daemon():
        aft_pid_libvirtd = process.getoutput("pidof libvirtd")
        if not utils_libvirtd.libvirtd_is_running() or ori_pid_libvirtd != aft_pid_libvirtd:
            test.fail("Libvirtd crash after attaching ccw addr devices")


def create_same_pci_slot_disk(params):
    """
    Create disk with existed pci slot

    :param params: dict wrapped with params
    """
    type_name = params.get("type_name")
    disk_device = params.get("device_type")
    device_target = params.get("target_dev")
    device_bus = params.get("target_bus")
    device_format = params.get("target_format")
    source_file_path = params.get("virt_disk_device_source")
    disk_src_dict = {"attrs": {"file": source_file_path}}
    vm_name = params.get("main_vm")

    if source_file_path:
        libvirt.create_local_disk("file", source_file_path, 1, device_format)
    pci_slot_addr_disk = libvirt_disk.create_primitive_disk_xml(
        type_name, disk_device,
        device_target, device_bus,
        device_format, disk_src_dict, None)

    # Get existed disk address
    addr = vm_xml.VMXML.get_disk_address(vm_name, 'vda')
    if not addr:
        raise Exception("Failed to get vda disk address")

    pci_addr_dict = tr_pci_address_to_dict(addr)
    pci_slot_addr_disk.address = pci_slot_addr_disk.new_disk_address(
        **{"attrs": pci_addr_dict})
    logging.debug("create_pci_slot_addr_disk xml: %s", pci_slot_addr_disk)
    return pci_slot_addr_disk


def tr_pci_address_to_dict(pci_addr_str):
    """
    Translate string format pci address to dict, and increment function element

    :param pci_addr_str: pci address in string format
    """
    addr_pci = pci_addr_str.split(":")[0]
    addr_misc_list = pci_addr_str.split(":")[1].split('.')
    addr_dict = {}
    addr_dict.update({'type': addr_pci})
    addr_dict.update({'domain': addr_misc_list[0]})
    addr_dict.update({'bus': addr_misc_list[1]})
    addr_dict.update({'slot': addr_misc_list[2]})
    # Increment function in hex format
    val = int(addr_misc_list[3], base=16)
    addr_dict.update({'function': str(hex(val + 1))})
    return addr_dict


def check_multifunction_is_on(vm_name, test):
    """
    Check multifunction is on

    :param vm_name: VM name
    :param test: test assert object
    """
    result = virsh.dumpxml(vm_name).stdout_text.strip()
    logging.debug("test dumpxml:%s", result)
    pattern_str = r"multifunction=.*on.*"
    if not re.search(pattern_str, result):
        test.fail("multifunction should be set to 'on' if two disks share pci slot")


def run(test, params, env):
    """
    Test attach device with ccw address option.

    1.Prepare test environment,destroy or suspend a VM.
    2.Prepare test xml for different devices.
    3.Perform test operation.
    4.Recover test environment.
    5.Confirm the test result.
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    virsh_dargs = {'debug': True, 'ignore_status': True}

    # Disk specific attributes.
    image_path = params.get("virt_disk_device_source", "/var/lib/libvirt/images/test.img")
    backend_device = params.get("backend_device", "disk")
    logging.debug("eval devei backed:%s", backend_device)

    hotplug = "yes" == params.get("virt_device_hotplug")
    status_error = "yes" == params.get("status_error")
    define_error = "yes" == params.get("define_error", "no")
    expected_fails_msg = []
    error_msg = params.get("error_msg", "cannot use CCW address type for device")
    expected_fails_msg.append(error_msg)

    device_obj = None
    ori_pid_libvirtd = None

    # Back up xml file.
    if vm.is_alive():
        vm.destroy(gracefully=False)
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    try:
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        if backend_device == "disk":
            device_obj = create_ccw_addr_disk(params)
        elif backend_device == "rng":
            device_obj = create_ccw_addr_rng(params)
        elif backend_device == "controller":
            device_obj = create_ccw_addr_controller(params)
        elif backend_device == "same_pci_slot":
            device_obj = create_same_pci_slot_disk(params)
        # Check libvirtd should not crash during the process
        if not utils_split_daemons.is_modular_daemon():
            ori_pid_libvirtd = process.getoutput("pidof libvirtd")
        if not hotplug:
            # Sync VM xml.
            vmxml.add_device(device_obj)
            vmxml.sync()
        vm.start()
        vm.wait_for_login().close()
        if status_error:
            if hotplug:
                logging.info("attaching devices, expecting error...")
                result = virsh.attach_device(vm_name, device_obj.xml, debug=True)
                libvirt.check_result(result, expected_fails=expected_fails_msg)
            else:
                test.fail("VM started unexpectedly.")
    except virt_vm.VMStartError as e:
        if status_error:
            if hotplug:
                test.fail("In hotplug scenario, VM should "
                          "start successfully but not."
                          "Error: %s", str(e))
            else:
                logging.debug("VM failed to start as expected."
                              "Error: %s", str(e))
        else:
            test.fail("VM failed to start."
                      "Error: %s" % str(e))
        check_libvirtd_process_id(ori_pid_libvirtd, test)
    except xcepts.LibvirtXMLError as xml_error:
        if not define_error:
            test.fail("Failed to define VM:\n%s" % xml_error)
        else:
            logging.info("As expected, failed to define VM")
        check_libvirtd_process_id(ori_pid_libvirtd, test)
    except Exception as ex:
        test.fail("unexpected exception happen: %s" % str(ex))
        check_libvirtd_process_id(ori_pid_libvirtd, test)
    else:
        if backend_device == "same_pci_slot":
            check_multifunction_is_on(vm_name, test)
    finally:
        # Recover VM.
        if vm.is_alive():
            vm.destroy(gracefully=False)
        logging.info("Restoring vm...")
        vmxml_backup.sync()
        # Clean up images
        for file_path in [image_path]:
            if os.path.exists(file_path):
                os.remove(file_path)
