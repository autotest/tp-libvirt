import aexpect
import logging
import re
import os

from avocado.utils import process

from virttest import remote
from virttest import virt_vm

from virttest.libvirt_xml import vm_xml, xcepts

from virttest.utils_libvirt import libvirt_disk
from virttest.utils_nbd import NbdExport

LOG = logging.getLogger('avocado.' + __name__)
cleanup_files = []


def create_customized_nbd_disk(params, nbd_server_host):
    """
    Create one customized nbd disk with related attributes

    :param params: dict wrapped with params
    :param nbd_server_host: nbd server hostname
    """
    type_name = params.get("type_name")
    disk_device = params.get("device_type")
    device_bus = params.get("target_bus")
    device_format = params.get("target_format")
    device_target = params.get("target_dev")
    rerror_policy_value = params.get("rerror_policy_value")
    source_dict = {}
    disk_src_dict = {}

    # Prepare disk source xml
    source_dict.update({"protocol": "nbd", "tls": "no"})
    disk_src_dict.update({"attrs": source_dict})
    disk_src_dict.update({"hosts": [{"name": nbd_server_host, "port": '10001'}]})

    customized_disk = libvirt_disk.create_primitive_disk_xml(
        type_name, disk_device,
        device_target, device_bus,
        device_format, disk_src_dict, None)
    if rerror_policy_value:
        customized_disk.driver = dict(customized_disk.driver, **{'rerror_policy': rerror_policy_value})

    copy_on_read = params.get("copy_on_read")
    if copy_on_read:
        customized_disk.driver = dict(customized_disk.driver, **{'copy_on_read': copy_on_read})

    device_readonly = "yes" == params.get("readonly", "no")
    if device_readonly:
        customized_disk.readonly = True

    LOG.debug("create customized xml: %s", customized_disk)
    return customized_disk


def operate_guest_disk_after_killing_nbdserver(vm):
    """
    Operate guest disk after killing nbd server.

    :params vm: VM instance
    """
    try:
        session = vm.wait_for_login()
        nbd_disk_name, _ = libvirt_disk.get_non_root_disk_name(session)
        process.run("pidof qemu-nbd && killall qemu-nbd",
                    ignore_status=True, shell=True)
        # Execute read disk operation
        cmd = "dd if=/dev/%s of=file" % nbd_disk_name
        session.cmd_status_output(cmd)
        session.close()
    except (remote.LoginError, virt_vm.VMError, aexpect.ShellError) as e:
        LOG.error(str(e))
        session.close()


def check_dmeg_and_domblkerror(params, vm, test, check_error_msg=True):
    """
    Check related information in dmesg output

    :param params: config parameters in dict
    :param vm: VM instance
    :param test: test object
    :param check_error_msg: one boolean variable indicating whether need check error message
    """
    error_msg = params.get("error_msg")
    device_target = params.get("target_dev")

    operate_guest_disk_after_killing_nbdserver(vm)

    def _check_dmeg_msg(error_msg):
        """
        Check whether error_msg in dmesg output

        :param error_msg: error message
        """
        try:
            session = vm.wait_for_login()
            cmd = ("dmesg| tail")
            status, output = session.cmd_status_output(cmd)
            LOG.debug("Disk operation in VM:\nexit code:\n%s\noutput:\n%s",
                      status, output)
            session.cmd("dmesg -C")
            if status != 0:
                return False
        except (remote.LoginError, virt_vm.VMError, aexpect.ShellError) as e:
            LOG.error(str(e))
            session.cmd("dmesg -C")
            return False
        else:
            return re.search(r'%s' % error_msg, output)

    result = _check_dmeg_msg(error_msg)
    if check_error_msg:
        if not result:
            test.fail("Failed to get expected message: %s" % error_msg)
    else:
        if result:
            test.fail("Unexpected messages: %s are found" % error_msg)


def run(test, params, env):
    """
    Test start Vm with nbd disk with setting rerror_policy.

    1.Prepare test environment,destroy or suspend a VM.
    2.Prepare test xml.
    3.Perform test operation.
    4.Recover test environment.
    5.Confirm the test result.
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    # Disk specific attributes.
    rerror_policy_value = params.get("rerror_policy_value")
    image_path = params.get("virt_disk_device_source")
    cleanup_files.append(image_path)

    hotplug = "yes" == params.get("virt_device_hotplug")
    status_error = "yes" == params.get("status_error")
    define_error = "yes" == params.get("define_error", "no")

    # Back up xml file.
    if vm.is_alive():
        vm.destroy(gracefully=False)
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    try:
        # Get server hostname.
        nbd_server_host = process.run('hostname', ignore_status=False, shell=True, verbose=True).stdout_text.strip()

        # Create NbdExport object
        nbd = NbdExport(image_path, image_size="100M")
        nbd.start_nbd_server()

        # Create disk XML as required
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        disk_xml_obj = create_customized_nbd_disk(params, nbd_server_host)

        if not hotplug:
            # Sync VM xml.
            vmxml.add_device(disk_xml_obj)
            vmxml.sync()
        vm.start()
        vm.wait_for_login().close()
    except virt_vm.VMStartError as e:
        if status_error:
            LOG.debug("VM failed to start as expected."
                      "Error: %s", str(e))
        else:
            test.fail("VM failed to start."
                      "Error: %s" % str(e))
    except xcepts.LibvirtXMLError as xml_error:
        if not define_error:
            test.fail("Failed to define VM:\n%s" % str(xml_error))
    except Exception as ex:
        test.fail("unexpected exception happen: %s" % str(ex))
    else:
        if rerror_policy_value == "ignore":
            check_dmeg_and_domblkerror(params, vm, test, check_error_msg=False)
        elif rerror_policy_value == "report":
            check_dmeg_and_domblkerror(params, vm, test, check_error_msg=True)
        elif rerror_policy_value == "stop":
            process.run("pidof qemu-nbd && killall qemu-nbd",
                        ignore_status=True, shell=True)
            if not vm.pause():
                test.fail("VM should be paused if rerror_policy='stop'")
    finally:
        try:
            if nbd:
                nbd.cleanup()
        except Exception as ndbEx:
            LOG.info("Clean up nbd failed: %s", str(ndbEx))
        # Recover VM
        LOG.info("Restoring vm...")
        if vm.is_alive():
            vm.destroy(gracefully=False)
        vmxml_backup.sync()
        # Clean up images
        for file_path in cleanup_files:
            if os.path.exists(file_path):
                os.remove(file_path)
