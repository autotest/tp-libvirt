import logging
import os

from avocado.core import exceptions

from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


def get_xmlinfo(vm_name, options):
    """
    Get some iothreadinfo from the guests xml
    Returns:
        List of iothread ids
    """
    if "--config" in options:
        xml_info = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    else:
        xml_info = vm_xml.VMXML.new_from_dumpxml(vm_name)
    logging.debug("domxml: %s", xml_info)
    return xml_info.iothreadids.iothread


def prepare_disk(path, disk_format, disk_size):
    """
    Prepare the disk for a given disk format.
    """
    # Check if we test with a non-existed disk.
    if os.path.exists(path):
        return path
    else:
        device_source = libvirt.create_local_disk(
            "file", path, disk_size, disk_format=disk_format)
    return device_source


def run(test, params, env):
    """
    Test command: virsh iothreaddel and related disk attach detach operations.

    The command can change the number of iothread.
    1.Prepare test environment,destroy or suspend a VM.
    2.Perform virsh iothreaddel operation.
    3.Recover test environment.
    4.Confirm the test result.
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    pre_vm_state = params.get("iothread_pre_vm_state")
    command = params.get("iothread_command", "iothreaddel")
    options = params.get("iothread_options")
    vm_ref = params.get("iothread_vm_ref", "name")
    iothreads = params.get("iothreads", 4)
    iothread_id = params.get("iothread_id", "2")
    disk_iothread_id = params.get("disk_iothread_id", "0")
    status_error = "yes" == params.get("status_error")
    iothreadids = params.get("iothreadids")
    iothreadpins = params.get("iothreadpins")
    disk_prepare = "yes" == params.get("disk_prepare", "no")
    disk_name = params.get("disk_name")
    disk_format = params.get("disk_format")
    disk_size = params.get("disk_size")
    device_target = params.get("device_target", "vdb")
    detach_disk = "yes" == params.get("detach_disk", "no")
    attach_disk = "yes" == params.get("attach_disk", "no")
    try:
        iothreads = int(iothreads)
    except ValueError:
        # 'iothreads' may not invalid number in negative tests
        logging.debug("Can't convert %s to integer type", iothreads)
    # Save original configuration
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    orig_config_xml = vmxml.copy()
    try:
        if vm.is_alive():
            vm.destroy()

        option_list = options.split(" ")
        for item in option_list:
            if virsh.has_command_help_match(command, item) is None:
                raise exceptions.TestSkipError("The current libvirt version"
                                               " doesn't support '%s' option"
                                               % item)
        # Set iothreads first
        if iothreadids:
            ids_xml = vm_xml.VMIothreadidsXML()
            ids_xml.iothread = iothreadids.split()
            vmxml.iothreadids = ids_xml
        if iothreadpins:
            cputune_xml = vm_xml.VMCPUTuneXML()
            io_pins = []
            for pins in iothreadpins.split():
                thread, cpuset = pins.split(':')
                io_pins.append({"iothread": thread,
                                "cpuset": cpuset})
            cputune_xml.iothreadpins = io_pins
            vmxml.cputune = cputune_xml
        vmxml.iothreads = iothreads
        logging.debug("Pre-test xml is %s", vmxml)
        vmxml.sync()
        # Restart, unless that's not our test
        if not vm.is_alive():
            vm.start()
        vm.wait_for_login()
        domid = vm.get_id()  # only valid for running
        domuuid = vm.get_uuid()
        if pre_vm_state == "shut off" and vm.is_alive():
            vm.destroy()
        if disk_prepare:
            # Prepare disk and run disk relevent test
            path = os.path.join(test.tmpdir, disk_name)
            source_disk = prepare_disk(path, disk_format, disk_size)
            attach_options = "--iothread " + iothread_id
            if attach_disk and status_error:
                # Check for BZ1253108
                attach_options = "--iothread " + disk_iothread_id
            cmd_result = virsh.attach_disk(vm_name, source_disk, device_target, attach_options)
            if attach_disk:
                libvirt.check_exit_status(cmd_result, status_error)
            if detach_disk:
                if status_error:
                    # try iothreaddel when iothread in use
                    cmd_result = virsh.iothreaddel(vm_name, iothread_id, options,
                                                   ignore_status=True, debug=True)
                else:
                    cmd_result = virsh.detach_disk(vm_name, device_target)
                libvirt.check_exit_status(cmd_result, status_error)
        else:
            # Run iothreaddel disk irrelevent test
            if vm_ref == "name":
                dom_option = vm_name
            elif vm_ref == "id":
                dom_option = domid
            elif vm_ref == "uuid":
                dom_option = domuuid
            else:
                dom_option = vm_ref
            ret = virsh.iothreaddel(dom_option, iothread_id, options,
                                    ignore_status=True, debug=True)
            libvirt.check_exit_status(ret, status_error)
    finally:
        # Cleanup
        if vm.is_alive():
            vm.destroy()
        orig_config_xml.sync()
        if disk_prepare:
            libvirt.delete_local_disk("file", source_disk)
