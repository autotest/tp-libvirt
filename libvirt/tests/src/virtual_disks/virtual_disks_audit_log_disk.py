import logging
import os

from avocado.utils import process
from avocado.utils import service

from virttest import virt_vm
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest import utils_libvirtd
from virttest import utils_split_daemons

from virttest.utils_libvirt import libvirt_disk
from virttest.utils_test import libvirt


from virttest.utils_config import LibvirtdConfig
from virttest.utils_config import VirtQemudConfig

LOG = logging.getLogger('avocado.' + __name__)

cleanup_files = []


def create_customized_disk(params):
    """
    Create one customized disk with related attributes

    :param params: dict wrapped with params
    :return: return disk device
    """
    type_name = params.get("type_name")
    device_target = params.get("target_dev")
    disk_device = params.get("device_type")
    device_bus = params.get("target_bus")
    device_format = params.get("target_format")
    source_file = params.get("virt_disk_device_source")
    source_dict = {}
    if source_file:
        libvirt.create_local_disk("file", source_file, 1, device_format)
        cleanup_files.append(source_file)
        source_dict.update({"file": source_file})

    disk_src_dict = {"attrs": source_dict}

    customized_disk = libvirt_disk.create_primitive_disk_xml(
        type_name, disk_device,
        device_target, device_bus,
        device_format, disk_src_dict, None)

    LOG.debug("create customized xml: %s", customized_disk)
    return customized_disk


def enable_audit_log(libvirtd_config):
    """
    Configure audit log level"

    :param libvirtd_config: libvirtd config object
    """
    libvirtd_config.audit_level = 1
    libvirtd_config.audit_logging = 1
    utils_libvirtd.Libvirtd('virtqemud').restart()


def clean_up_audit_log_file():
    """
    Clean up audit message in log file.
    """
    cmd = "truncate -s 0  /var/log/audit/audit.log*"
    process.run(cmd, shell=True)


def ensure_auditd_started():
    """
    Check audit service status and start it if it's not running
    """
    service_name = 'auditd'
    service_mgr = service.ServiceManager()
    status = service_mgr.status(service_name)
    LOG.debug('Service status is %s', status)
    if not status:
        service_mgr.start(service_name)


def check_disk_message_from_audit_log(key_message, test):
    """
    Check whether disk related message in /var/log/audit/audit.log

    :param key_message: key message that needs to be captured
    :param test: test object instance
    """
    cmd = 'ausearch --start today -m VIRT_RESOURCE -i | grep %s' % key_message
    if process.system(cmd, ignore_status=True, shell=True):
        test.fail("Check disk message failed in audit log by command: %s" % cmd)


def run(test, params, env):
    """
    Test start Vm, and check audit log related to guest disk

    1.Prepare test environment with provisioned VM
    2.Prepare test xml.
    3.Perform attach/detach disk, check audit log
    4.Recover test environment.
    5.Confirm the test result.
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    hotplug = "yes" == params.get("virt_device_hotplug")
    status_error = "yes" == params.get("status_error")

    libvirtd_config = VirtQemudConfig() if utils_split_daemons.is_modular_daemon() else LibvirtdConfig()

    # Back up xml file
    if vm.is_alive():
        vm.destroy(gracefully=False)
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    try:
        enable_audit_log(libvirtd_config)
        clean_up_audit_log_file()
        ensure_auditd_started()
        vm.start()

        # Create disk xml
        device_obj = create_customized_disk(params)
        if hotplug:
            virsh.attach_device(vm_name, device_obj.xml, ignore_status=False, debug=True)
        vm.wait_for_login().close()
    except virt_vm.VMStartError as e:
        if status_error:
            LOG.debug("VM failed to start as expected."
                      "Error: %s", str(e))
        else:
            test.fail("VM failed to start."
                      "Error: %s" % str(e))
    else:
        check_disk_message_from_audit_log('attach', test)
        virsh.detach_disk(vm_name, params.get("target_dev"), ignore_status=False)
        vm.wait_for_login().close()
        check_disk_message_from_audit_log('detach', test)
    finally:
        # Recover VM
        LOG.info("Restoring vm...")
        if vm.is_alive():
            vm.destroy(gracefully=False)
        vmxml_backup.sync()
        # Restore libvirtd
        libvirtd_config.restore()
        utils_libvirtd.Libvirtd('virtqemud').restart()
        # Clean up files
        for file_path in cleanup_files:
            if os.path.exists(file_path):
                os.remove(file_path)
