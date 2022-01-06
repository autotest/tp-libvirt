import logging
import os
import time
from virttest import data_dir
from virttest import utils_disk
from virttest import utils_misc
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Test disk IOThread.

    1.Prepare test environment, destroy or suspend a VM.
    2.Perform test operation.
    3.Recover test environment.
    4.Confirm the test result.
    """
    # Step 1. Prepare test environment.
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    # Back up xml file.
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    orig_config_xml = vmxml_backup.copy()

    def prepare_vmxml_and_test_disk():
        """
        Configure the XML definition of the VM under test to include
        the required starting iothread value, and prepare
        a test disk for usage.
        """
        iothreads = params.get("iothreads", 5)
        try:
            iothreads = int(iothreads)
        except ValueError:
            # 'iothreads' must be convertible to an integer
            logging.debug("Can't convert %s to integer type", iothreads)

        # Set iothreads first
        vmxml_backup.iothreads = iothreads
        logging.debug("Pre-test xml is %s", vmxml_backup)
        vmxml_backup.sync()

        device_source_name = \
            params.get("virt_disk_device_source", "attach.img")
        device_source_format = \
            params.get("virt_disk_device_source_format", "qcow2")
        device_source_path = \
            os.path.join(data_dir.get_data_dir(), device_source_name)
        device_source = libvirt.create_local_disk(
                    "file", path=device_source_path,
                    size="1", disk_format=device_source_format)

        # Disk specific attributes.
        device_target = params.get("virt_disk_device_target", "vdb")

        return device_source_path, device_source, device_target

    def attach_disk_iothread_zero(vm_name, device_source, device_target):
        """
        Attempt to attach a disk with an iothread value of 0.
        """
        disk_errors = \
            params.get("virt_disk_iothread_0_errors")
        attach_option = params.get("disk_attach_option_io_0", "--iothread 0")
        ret = virsh.attach_disk(vm_name, device_source,
                                device_target,
                                attach_option, debug=True)
        disk_attach_error = True
        libvirt.check_exit_status(ret, disk_attach_error)
        libvirt.check_result(ret, expected_fails=disk_errors)

    def attach_disk_iothread_two(vm_name, device_source, device_target):
        """
        Attach a disk with an iothread value of 2.
        """
        disk_attach_success = params.get("virt_disk_attach_success")
        attach_option = params.get("disk_attach_option_io_2")
        ret = virsh.attach_disk(vm_name, device_source,
                                device_target,
                                attach_option, debug=True)
        disk_attach_error = False
        libvirt.check_exit_status(ret, disk_attach_error)
        libvirt.check_result(ret, expected_match=disk_attach_success)

    def check_iothread_in_xml(vm_name):
        """
        Check that the iothread value has been set in the XML file.
        """
        dom_xml = virsh.dumpxml(vm_name, debug=False).stdout_text.strip()
        iothread_str = \
            params.get("xml_iothread_block")
        if iothread_str not in dom_xml:
            test.fail("IOThread value was not set to 2 in %s" % dom_xml)

    def delete_busy_iothread(vm_name):
        """
        Attempt to delete a busy iothread disk.
        """
        thread_id = params.get("virt_disk_thread_id", "--id 2")
        ret = virsh.iothreaddel(vm_name, thread_id)
        iothread_error = True
        disk_errors = \
            params.get("virt_disk_iothread_in_use_error")
        libvirt.check_exit_status(ret, iothread_error)
        libvirt.check_result(ret, expected_fails=disk_errors)

    def detach_disk(vm_name, device_target):
        """
        Detach a virtual disk.
        """
        disk_detach_success = params.get("virt_disk_detach_success")
        ret = virsh.detach_disk(vm_name, device_target)
        disk_detach_error = False
        libvirt.check_exit_status(ret, disk_detach_error)
        libvirt.check_result(ret, expected_match=disk_detach_success)

        def _check_disk_detach():
            try:
                if device_target not in utils_disk.get_parts_list():
                    return True
                else:
                    logging.debug("Target device is still "
                                  "present after detach command")
            except Exception:
                return False

        # If disk_detach_error is False, then wait a few seconds
        # to let detach operation complete
        if not disk_detach_error:
            utils_misc.wait_for(_check_disk_detach, timeout=20)

    try:
        if vm.is_alive():
            vm.destroy()

        device_source_path, device_source, device_target = \
            prepare_vmxml_and_test_disk()

        # Restart the vm.
        if not vm.is_alive():
            vm.start()
            vm.wait_for_login()

        # Step 2. Perform test operations.
        # Test operation 1.
        # Attach disk with --iothread 0
        attach_disk_iothread_zero(vm_name, device_source, device_target)

        # Test operation 2.
        # Attach disk of type qcow2 --iothread 2
        attach_disk_iothread_two(vm_name, device_source, device_target)

        # Test operation 3.
        # Check iothread has been set to 2 in XML.
        check_iothread_in_xml(vm_name)

        # Test operation 4.
        # Delete an IOThread which was assigned to a disk.
        delete_busy_iothread(vm_name)

        # Test operation 5. Detach a disk.
        # On hardware, the previous step results in the power
        # indicator light on the PCIE slot to blink
        # for 5 seconds. This allows the user time to cancel
        # the command. This causes the following detach command
        # to fail. We must wait 6 seconds until the blinking
        # has stopped.
        time.sleep(6)
        detach_disk(vm_name, device_target)

    finally:
        # Step 3. Recover Test environment.
        if vm.is_alive():
            vm.destroy(gracefully=False)
        orig_config_xml.sync()
        libvirt.delete_local_disk("file", device_source)
