import logging
import os
import random
import re
import string
import aexpect
import time

from avocado.utils import linux_modules
from avocado.utils import process
from avocado.utils import service

from virttest import libvirt_version
from virttest import virt_vm, utils_misc
from virttest import virsh
from virttest import utils_split_daemons
from virttest import utils_libvirtd

from virttest.libvirt_xml import vm_xml, xcepts

from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_disk

from virttest.utils_config import LibvirtdConfig
from virttest.utils_config import VirtQemudConfig


LOG = logging.getLogger('avocado.' + __name__)
CLEANUP_FILES = []


def create_customized_disk(params):
    """
    Create one customized disk with related attributes

    :param params: dict wrapped with params
    """
    type_name = params.get("type_name")
    disk_device = params.get("device_type")
    device_target = params.get("target_dev")
    device_bus = params.get("target_bus")
    device_format = params.get("target_format")
    source_file_path = params.get("virt_disk_device_source")
    source_dict = {}
    if source_file_path:
        if 'block' in type_name:
            source_dict.update({"dev": source_file_path})
        else:
            source_dict.update({"file": source_file_path})
    startup_policy = params.get("startupPolicy")
    if startup_policy:
        source_dict.update({"startupPolicy": startup_policy})
    disk_src_dict = {"attrs": source_dict}

    addr_str = params.get("addr_attrs")

    customized_disk = libvirt_disk.create_primitive_disk_xml(
        type_name, disk_device,
        device_target, device_bus,
        device_format, disk_src_dict, None)
    if addr_str:
        addr_dict = eval(addr_str)
        customized_disk.address = customized_disk.new_disk_address(
            **{"attrs": addr_dict})
    target_tray = params.get("tray")
    if target_tray:
        customized_disk.target = dict(customized_disk.target, **{'tray': target_tray})
    copy_on_read = params.get("copy_on_read")
    if copy_on_read:
        customized_disk.driver = dict(customized_disk.driver, **{'copy_on_read': copy_on_read})
    LOG.debug("create customized xml: %s", customized_disk)
    return customized_disk


def create_file_lun_source_disk(params):
    """
    Create one file lun source disk

    :param params: dict wrapped with params
    """
    device_format = params.get("target_format")
    source_file_path = params.get("virt_disk_device_source")

    if source_file_path:
        libvirt.create_local_disk("file", source_file_path, 1, device_format)
    file_lun_source_disk = create_customized_disk(params)

    return file_lun_source_disk


def create_https_cdrom_disk(params):
    """
    Create one HTTPS cdrom disk

    :param params: dict wrapped with params
    """
    default_iso_name = params.get('default_iso_name')
    if 'EXAMPLE_HTTPS_ISO' in params.get("source_name"):
        params.update({'source_name': default_iso_name})

    https_cdrom_disk = libvirt.create_disk_xml(params)
    return https_cdrom_disk


def check_https_cdrom_device_mounted(vm, test):
    """
    Check cdrom device in VM can be mounted

    :param vm: one object representing VM
    :param test: test assert object
    """
    session = vm.wait_for_login()
    check_cdrom_device_cmd = "ls  /dev/cdrom"
    utils_misc.wait_for(lambda: not session.cmd_status(check_cdrom_device_cmd), 60)
    mnt_cmd = "mount /dev/cdrom /mnt && ls /mnt/LICENSE"
    status = session.cmd_status(mnt_cmd)
    if status:
        test.fail("Failed to mount cdrom device in VM")


def create_iso_cdrom_disk(params, create_iso=True):
    """
    Create one iso cdrom disk

    :param params: dict wrapped with parameter
    :param create_iso: one boolean value(default True)indicating whether create iso files
    """
    source_files_list = []
    source_file_path = params.get("virt_disk_device_source")
    source_files_list.append(source_file_path)
    source_file_path_second = params.get("virt_disk_device_source_second")
    if source_file_path_second:
        source_files_list.append(source_file_path_second)

    # Create iso files
    if source_file_path and create_iso:
        for iso_path in source_files_list:
            iso_file = "/var/lib/libvirt/images/old_%s.img" % random.choices(string.ascii_uppercase)[0]
            process.run("dd if=/dev/urandom of=%s bs=1M count=10" % iso_file, shell=True)
            process.run("mkisofs -o %s %s" % (iso_path, iso_file), shell=True)
            CLEANUP_FILES.append(iso_file)
            CLEANUP_FILES.append(iso_path)

    iso_cdrom_disk = create_customized_disk(params)
    return iso_cdrom_disk


def check_iso_cdrom_device_updated(vm, params, test):
    """
    Check iso cdrom device in VM can be updated

    :param vm: one object representing VM
    :param params: wrapped parameters in dictionary format
    :param test: test assert object
    """
    session = vm.wait_for_login()
    eject_cdrom_device_cmd = "eject /dev/sr0"
    status = session.cmd_status(eject_cdrom_device_cmd)
    if status:
        test.fail("Failed to eject cdrom device in VM")
    # Save and restore the guest
    tmp_save_path = "/tmp/save"
    virsh.save(vm.name, tmp_save_path, ignore_status=False)
    virsh.restore(tmp_save_path, ignore_status=False)
    vm.wait_for_login().close()
    device_target = params.get("target_dev")
    another_iso_path = params.get("virt_disk_device_source_second")
    virsh.change_media(vm.name, device_target, another_iso_path, ignore_status=False)

    # Check cdrom has been changed with new iso file
    check_source_in_cdrom_device(vm, another_iso_path, test)


def check_source_in_cdrom_device(vm, expected_file, test):
    """
    Common method to check source in cdrom device

    :param vm: one object representing VM
    :param expected_file: expected file
    :param test: test object
    """
    #Check cdrom has been changed with new iso file
    cdrom_vmxml = vm_xml.VMXML.new_from_dumpxml(vm.name)
    cdrom_devices = cdrom_vmxml.get_disk_all_by_expr('device==cdrom')
    cdrom_xml = list(cdrom_devices.values())[0]
    source_elemment = cdrom_xml.find('source')
    if expected_file is None:
        if source_elemment is not None:
            src_file = source_elemment.get('file')
            if src_file is not None:
                test.fail("actual iso cdrom is not empty")
    else:
        src_file = source_elemment.get('file') if source_elemment.get('file') is not None else source_elemment.get('dev')
        if expected_file != src_file:
            test.fail("actual iso cdrom: %s is not updated as expected: %s" % (src_file, expected_file))


def check_twice_iso_cdrom_device_updated(vm, first_iso_disk, params, test):
    """
    Check iso cdrom device in VM can be updated twice

    :param vm: one object representing VM
    :param first_iso_disk: disk xml with iso
    :param params: wrapped parameters in dictionary format
    :param test: test assert object
    """
    vm.wait_for_login().close()
    device_target = params.get("target_dev")
    first_iso_path = params.get("virt_disk_device_source")
    another_iso_path = params.get("virt_disk_device_source_second")

    # Create second iso disk, and update VM with it
    params.update({"virt_disk_device_source": params.get("virt_disk_device_source_second")})
    iso_cdrom_disk_second = create_iso_cdrom_disk(params, create_iso=False)
    virsh.update_device(vm.name, iso_cdrom_disk_second.xml, ignore_status=False)

    # Check cdrom has been changed with new iso file
    check_source_in_cdrom_device(vm, another_iso_path, test)

    # Restore to previous iso file
    vm.wait_for_login().close()
    virsh.update_device(vm.name, first_iso_disk.xml, ignore_status=False)

    # Check cdrom has been restored back previous iso
    check_source_in_cdrom_device(vm, first_iso_path, test)


def check_requisite_startuppolicy_cdrom(vm, params, test):
    """
    Check iso cdrom device with startup policy in VM after iso file is renamed

    :param vm: one object representing VM
    :param params: wrapped parameters in dictionary format
    :param test: test assert object
    """
    first_iso_path = params.get("virt_disk_device_source")
    renamed_first_iso_path = "%s.bak" % first_iso_path
    os.rename(first_iso_path, renamed_first_iso_path)
    # Save and restore the guest
    tmp_save_path = "/tmp/save"
    virsh.save(vm.name, tmp_save_path, ignore_status=False)
    virsh.restore(tmp_save_path, ignore_status=False)
    vm.wait_for_login().close()
    # Cdrom should be empty
    check_source_in_cdrom_device(vm, None, test)

    # Create second iso disk, and update VM with it
    if 'tray' in params:
        params.pop('tray')
    params.update({"virt_disk_device_source": params.get("virt_disk_device_source_second")})
    iso_cdrom_disk_second = create_iso_cdrom_disk(params, create_iso=False)
    virsh.update_device(vm.name, iso_cdrom_disk_second.xml, flagstr="--live", debug=True)
    vm.wait_for_login().close()


def create_open_tray_cdrom_disk(params):
    """
    Create one open tray cdrom disk

    :param params: dict wrapped with params
    """
    source_file_path = params.get("virt_disk_device_source")
    # Create iso file
    if source_file_path:
        iso_file = "/var/lib/libvirt/images/old_%s.img" % random.choices(string.ascii_uppercase)[0]
        process.run("dd if=/dev/urandom of=%s bs=1M count=10" % iso_file, shell=True)
        process.run("mkisofs -o %s %s" % (source_file_path, iso_file), shell=True)
        CLEANUP_FILES.append(iso_file)
        CLEANUP_FILES.append(source_file_path)
    open_tray_cdrom_disk = libvirt.create_disk_xml(params)
    return open_tray_cdrom_disk


def check_open_tray_cdrom(vm, params, test):
    """
    Check open tray cdrom device in VM can be updated

    :param vm: one object representing VM
    :param params: wrapped parameters in dictionary format
    :param test: test assert object
    """
    iso_file_path = params.get("virt_disk_device_source")
    device_target = params.get("target_dev")
    option = " %s --insert " % iso_file_path
    virsh.change_media(vm.name, device_target, option, ignore_status=False, debug=True)

    # Check open tray cdrom has been changed with iso file
    check_source_in_cdrom_device(vm, iso_file_path, test)

    option = " --eject "
    virsh.change_media(vm.name, device_target, option, wait_for_event=True, event_timeout=10,
                       ignore_status=False, debug=True)
    vm.wait_for_login().close()

    # Check cdrom has been changed with empty
    check_source_in_cdrom_device(vm, None, test)


def check_change_startuppolicy_cdrom_backend(vm, params, origin_device_xml, test):
    """
    Check live update cdrom with new source type and startupPolicy BZ2003644

    :param vm: one object representing VM
    :param params: wrapped parameters in dictionary format
    :param origin_device_xml: original device xml before updated
    :param test: test assert object
    """
    # Create block type cdrom disk, and update VM with it
    if 'startupPolicy' in params:
        params.pop('startupPolicy')

    origin_first_iso_path = params.get("virt_disk_device_source")

    # Load module and get scsi disk name
    utils_misc.wait_for(lambda: linux_modules.load_module("scsi_debug lbpu=1 lbpws=1"), timeout=10, ignore_errors=True)
    scsi_disk = process.run("lsscsi|grep scsi_debug|"
                            "awk '{print $6}'", shell=True).stdout_text.strip()

    params.update({"virt_disk_device_source": scsi_disk})
    params.update({"type_name": "block"})

    iso_cdrom_disk_second = create_iso_cdrom_disk(params, create_iso=False)

    virsh.update_device(vm.name, iso_cdrom_disk_second.xml, flagstr="--live", ignore_status=False, debug=True)
    vm.wait_for_login().close()
    # Cdrom should be updated
    check_source_in_cdrom_device(vm, scsi_disk, test)

    # Restore to original filed based one
    virsh.update_device(vm.name, origin_device_xml.xml, flagstr="--live", ignore_status=False, debug=True)
    vm.wait_for_login().close()
    # Cdrom should be restored
    check_source_in_cdrom_device(vm, origin_first_iso_path, test)


def check_libvirtd_not_crash_on_domstats(vm, old_pid_of_libvirtd, test):
    """
    Check libvirtd/virtqemud not crash before and after domstats

    :param vm: one object representing VM
    :param old_pid_of_libvirtd: previous libvirtd/virtqemud process id
    :param test: test assert object
    """
    # Execute check domain domstats
    virsh.domstats(vm.name, ignore_status=False, debug=True)
    virsh.domstats(vm.name, ignore_status=False, readonly=True, debug=True)

    # Check libvirtd/virtqemud not changed
    cmd = "pidof virtqemud" if utils_split_daemons.is_modular_daemon() else "pidof libvirtd"
    new_pid_of_libvirtd = process.run(cmd, shell=True).stdout_text.strip()
    if new_pid_of_libvirtd != old_pid_of_libvirtd:
        test.fail("libvirtd/virtqemud crashed due to processing")


def create_block_lun_source_disk(params):
    """
    Create one block lun source disk

    :param params: dict wrapped with params
    :return: return one lun source disk
    """
    source_file_path = params.get("virt_disk_device_source")
    if source_file_path:
        libvirt.create_local_disk("file", source_file_path, 1, 'raw')

    # Find a free loop device
    free_loop_dev = process.run("losetup --find", shell=True).stdout_text.strip()
    # Setup a loop device
    cmd = 'losetup %s %s' % (free_loop_dev, source_file_path)
    process.run(cmd, shell=True)

    params.update({"virt_disk_device_source": free_loop_dev})
    block_lun_source_disk = create_iso_cdrom_disk(params, create_iso=False)

    return block_lun_source_disk


def check_cdrom_reboot_reset(vm, params, test):
    """
    Check tray state can be updated after VM reboot or reset

    :param vm: one object representing VM
    :param params: wrapped parameters in dictionary format
    :param test: test assert object
    """
    # reboot and reset VM
    for virsh_function in [virsh.reboot, virsh.reset]:
        session = vm.wait_for_login()
        eject_cdrom_device_cmd = "eject /dev/sr0"
        status, output = session.cmd_status_output(eject_cdrom_device_cmd)
        if status:
            test.fail("Failed to eject cdrom device in VM with error message:%s" % output)

        def _get_tray_state():
            """
            Get tray state
            """
            cdrom_vmxml = vm_xml.VMXML.new_from_dumpxml(vm.name)
            cdrom_devices = cdrom_vmxml.get_disk_all_by_expr('device==cdrom')
            cdrom_xml = list(cdrom_devices.values())[0]
            tray_state = cdrom_xml.find('target').get('tray')
            LOG.debug("tray state value:\n %s", tray_state)

            return tray_state

        tray_state = _get_tray_state()
        if tray_state != "open":
            test.fail("Fail to eject guest cdrom, and expected tray state should be open, but actually: %s"
                      % tray_state)

        def _qemu_state():
            """
            Get qemu state
            """
            qemu_output = virsh.qemu_monitor_command(name=vm.name, cmd="info block", options='--hmp',
                                                     **{'debug': True, 'ignore_status': True})
            LOG.debug("qemu output:\n%s", qemu_output.stdout_text)
            return qemu_output.stdout_text

        tray_original_state_open = params.get("tray_original_state")
        qemu_state = _qemu_state()
        if tray_original_state_open not in qemu_state:
            test.fail("Failed since tray state is open, but get state from qemu: %s" % qemu_state)

        # start loop to wait for tray-change event
        virsh_session = aexpect.ShellSession(virsh.VIRSH_EXEC, auto_close=True)
        event_cmd = "event --domain %s --event tray-change --loop" % vm.name
        virsh_session.sendline(event_cmd)

        virsh_function(vm.name, ignore_status=False, debug=True)
        vm.wait_for_login().close()
        # check tray state again, should be closed, and value is None
        tray_state_update = _get_tray_state()
        if tray_state_update is not None:
            test.fail("Failed since expected tray state is closed, but get state is: %s" % tray_state_update)
        # check tray state from qemu
        tray_reset_state = params.get("tray_reset_state")
        qemu_state_update = _qemu_state()
        if tray_reset_state not in qemu_state_update:
            test.fail("Failed since expect tray state is closed, but from qemu is: %s" % qemu_state_update)

        # check tray-change event
        virsh_session.send_ctrl("^C")
        ret_output = virsh_session.get_stripped_output().replace("\n", "").strip()
        event_matched = r"event 'tray-change' for domain '%s' disk .*closed" % vm.name
        if not re.search(event_matched, ret_output):
            test.fail("Can not find matched event:%s from event output: %s" % (event_matched, ret_output))


def check_scsi_cdrom_hot_eject(vm, params, test):
    """
    Check eject hotplug scsi cdrom

    :param vm: one object representing VM
    :param params: wrapped parameters in dictionary format
    :param test: test assert object
    """
    # start loop to wait for tray-change event
    virsh_session = aexpect.ShellSession(virsh.VIRSH_EXEC, auto_close=True)
    event_cmd = "event --domain %s --event tray-change --loop" % vm.name
    virsh_session.sendline(event_cmd)
    time.sleep(60)
    device_target = params.get("target_dev")
    virsh.change_media(vm.name, device_target, " --eject --live",
                       ignore_status=False, debug=True)

    vm.wait_for_login().close()
    # Check cdrom has been changed with empty
    check_source_in_cdrom_device(vm, None, test)

    def _get_qemu_state():
        """
        Get qemu state
        """
        qemu_output = virsh.qemu_monitor_command(name=vm.name, cmd="info block", options='--hmp',
                                                 **{'debug': True, 'ignore_status': True})
        LOG.debug("qemu output:\n%s", qemu_output.stdout_text)
        return qemu_output.stdout_text

    tray_state_close = params.get("tray_state")
    qemu_state = _get_qemu_state()
    if tray_state_close not in qemu_state:
        test.fail("Failed since tray state is close, but get state from qemu: %s" % qemu_state)

    # check tray-change event
    virsh_session.send_ctrl("^C")
    ret_output = virsh_session.get_stripped_output().replace("\n", "").strip()
    LOG.debug("event:\n%s", ret_output)

    event_matched_opened = r"event 'tray-change' for domain '%s' disk .*opened" % vm.name
    if not re.search(event_matched_opened, ret_output):
        test.fail("Can not find matched event:%s from event output: %s" % (event_matched_opened, ret_output))
    event_matched_closed = r"event 'tray-change' for domain '%s' disk .*closed" % vm.name
    if not re.search(event_matched_closed, ret_output):
        test.fail("Can not find matched event:%s from event output: %s" % (event_matched_closed, ret_output))

    virsh.detach_disk(vm.name, device_target, " --live", ignore_status=False)
    cdrom_vmxml = vm_xml.VMXML.new_from_dumpxml(vm.name)
    cdrom_devices = cdrom_vmxml.get_disk_all_by_expr('device==cdrom')
    if len(cdrom_devices) > 0:
        test.fail("cdrom disk can not be detached successfully")


def check_empty_source_cdrom(vm, params, test):
    """
    Check hotplug empty source cdrom

    :param vm: one object representing VM
    :param params: wrapped parameters in dictionary format
    :param test: test assert object
    """
    vm.wait_for_login().close()
    # hotplug cdrom device
    device_obj = params.get("cdrom_xml")
    virsh.attach_device(vm.name, device_obj.xml, flagstr="--live", ignore_status=False, debug=True)

    # Check cdrom is present in disk xml
    check_source_in_cdrom_device(vm, None, test)


def check_dropped_changed_events_startuppolicy_backend(vm, params, virsh_session, test):
    """
    Check changed event for cdrom, and dropped event for disk

    :param vm: one object representing VM
    :param params: wrapped parameters in dictionary format
    :param virsh_session: virsh session
    :param test: test assert object
    """
    # check tray-change event
    virsh_session.send_ctrl("^C")
    ret_output = virsh_session.get_stripped_output().replace("\n", "").strip()
    LOG.debug("dropped and changed events:\n%s", ret_output)
    event_keywords = ["changed", "dropped"]

    for keyword in event_keywords:
        event_pattern = r"event 'disk-change' for domain '%s' disk .*%s" % (vm.name, keyword)
        if not re.search(event_pattern, ret_output):
            test.fail("Can not find matched event: %s from event output: %s" % (event_pattern, ret_output))


def create_block_cdrom_disk(params):
    """

    Create one block cdrom device
    :param params: dict wrapped with parameter
    """
    source_disk = libvirt.create_scsi_disk(scsi_option="",
                                           scsi_size="100")
    params.update({'virt_disk_device_source': source_disk})

    block_cdrom_disk = create_customized_disk(params)
    return block_cdrom_disk


def check_block_cdrom_log(vm, params, test):
    """
    Check matched information in vm.log

    :param vm: one object representing VM
    :param params: wrapped parameters in dictionary format
    :param test: test assert object
    """
    qemu_log = "/var/log/libvirt/qemu/%s.log" % vm.name
    libvirt.check_logfile(" tainted: cdrom-passthrough", qemu_log, str_in_log=False)


def create_disconnect_audit_cdrom(params):
    """

    Create one file cdrom device
    :param params: dict wrapped with parameter
    """
    libvirtd_config = VirtQemudConfig() if utils_split_daemons.is_modular_daemon() else LibvirtdConfig()
    libvirtd_config.audit_level = 1
    libvirtd_config.audit_logging = 1
    utils_libvirtd.Libvirtd('virtqemud').restart()

    # Clean up audit message in log file
    cmd = "truncate -s 0  /var/log/audit/audit.log*"
    process.run(cmd, shell=True)

    # ensure audit service is started
    service_name = 'auditd'
    service_mgr = service.ServiceManager()
    status = service_mgr.status(service_name)
    LOG.debug('Service status is %s', status)

    if not status:
        service_mgr.start(service_name)

    block_cdrom_disk = create_iso_cdrom_disk(params)
    return block_cdrom_disk


def check_disconnect_audit_cdrom(params, test):
    """

    check audit log when cdrom is disconnected
    :param params: dict wrapped with parameter
    :param test: test assert object
    """
    source_file_path = params.get("virt_disk_device_source")
    if os.path.exists(source_file_path):
        os.remove(source_file_path)

    def _check_disk_message_from_audit_log():
        """
        Check whether disk related message in /var/log/audit/audit.log
        """
        cmd = 'ausearch --start today -m VIRT_RESOURCE -i | grep update'
        return process.system(cmd, ignore_status=True, shell=True)

    result = utils_misc.wait_for(lambda: _check_disk_message_from_audit_log(), timeout=30)
    if not result:
        test.fail("Failed to get expected messages: virt resource update from log file: /var/log/audit/audit.log.")


def restore_libvirtd_config():
    """
    restore to previous libvirtd config
    """
    libvirtd_config = VirtQemudConfig() if utils_split_daemons.is_modular_daemon() else LibvirtdConfig()
    libvirtd_config.restore()
    utils_libvirtd.Libvirtd('virtqemud').restart()


def run(test, params, env):
    """
    Test attach cdrom device with option.

    1.Prepare test environment,destroy or suspend a VM.
    2.Prepare test xml for cdrom devices.
    3.Perform test operation.
    4.Recover test environment.
    5.Confirm the test result.
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    virsh_dargs = {'debug': True, 'ignore_status': True}

    # Disk specific attributes.
    image_path = params.get("virt_disk_device_source", "/var/lib/libvirt/images/test.img")
    CLEANUP_FILES.append(image_path)

    backend_device = params.get("backend_device", "disk")

    hotplug = "yes" == params.get("virt_device_hotplug")
    status_error = "yes" == params.get("status_error")
    define_error = "yes" == params.get("define_error", "no")
    expected_fails_msg = []
    error_msg = params.get("error_msg", "cannot use address type for device")
    expected_fails_msg.append(error_msg)

    libvirt_version.is_libvirt_feature_supported(params)

    device_obj = None
    # Back up xml file.
    if vm.is_alive():
        vm.destroy(gracefully=False)
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    try:
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        if backend_device == "file_lun_source":
            # Need start VM first
            vm.start()
            vm.wait_for_login().close()
            device_obj = create_file_lun_source_disk(params)
        elif backend_device == "https_cdrom_backend":
            device_obj = create_https_cdrom_disk(params)
            virsh.attach_device(vm_name, device_obj, flagstr="--config", debug=True)
        elif backend_device in ["iso_cdrom_backend", "twice_iso_cdrom_backend",
                                "requisite_startuppolicy_cdrom_backend",
                                "copy_on_read_not_compatible_with_readonly",
                                "change_startuppolicy_cdrom_backend",
                                "cdrom_reboot_reset_backend"]:
            device_obj = create_iso_cdrom_disk(params)
        elif backend_device == "open_tray_cdrom_backend":
            device_obj = create_open_tray_cdrom_disk(params)
            virsh.attach_device(vm_name, device_obj, flagstr="--config", debug=True)
        elif backend_device == "libvirtd_not_crash_on_domstats":
            cmd = "pidof virtqemud" if utils_split_daemons.is_modular_daemon() else "pidof libvirtd"
            old_pid_of_libvirtd = process.run(cmd, shell=True).stdout_text.strip()
            device_obj = create_iso_cdrom_disk(params)
            virsh.attach_device(vm_name, device_obj.xml, flagstr="--config", debug=True)
            device_target = params.get("target_dev")
            virsh.change_media(vm_name, device_target, " --eject --config",
                               ignore_status=False, debug=True)
        if backend_device == "block_lun_source":
            device_obj = create_block_lun_source_disk(params)
        if backend_device == "empty_source_cdrom_backend":
            device_obj = create_customized_disk(params)
            params.update({'cdrom_xml': device_obj})
        if backend_device == "dropped_changed_events_startuppolicy":
            # First create cdrom disk, then create one more file disk
            device_obj1 = create_customized_disk(params)
            vmxml.add_device(device_obj1)
            vmxml.sync()
            params.update({'virt_disk_device_source': params.get("virt_disk_device_source_second")})
            params.update({'target_dev': "sdd"})
            params.update({'device_type': 'disk'})
            device_obj = create_customized_disk(params)
            # start loop to wait for disk change event
            virsh_session = aexpect.ShellSession(virsh.VIRSH_EXEC, auto_close=True)
            event_cmd = "event --domain %s --event disk-change --loop" % vm.name
            virsh_session.sendline(event_cmd)
        if backend_device == "block_cdrom_tainted":
            device_obj = create_block_cdrom_disk(params)
        if backend_device == "disconnect_audit_cdrom_backend":
            device_obj = create_disconnect_audit_cdrom(params)
        if not hotplug:
            # Sync VM xml.
            vmxml.add_device(device_obj)
            vmxml.sync()
        vm.start()
        vm.wait_for_login().close()
        if backend_device == "scsi_cdrom_hot_eject_backend":
            device_obj = create_open_tray_cdrom_disk(params)
            virsh.attach_device(vm_name, device_obj, flagstr="--live", debug=True)
            iso_file_path = params.get("virt_disk_device_source")
            # Check cdrom has been filled with iso file
            check_source_in_cdrom_device(vm, iso_file_path, test)
        if status_error:
            if hotplug:
                LOG.info("attaching devices, expecting error...")
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
                LOG.debug("VM failed to start as expected."
                          "Error: %s", str(e))
        else:
            test.fail("VM failed to start."
                      "Error: %s" % str(e))
    except xcepts.LibvirtXMLError as xml_error:
        if not define_error:
            test.fail("Failed to define VM:\n%s" % xml_error)
        else:
            LOG.info("As expected, failed to define VM")
    except Exception as ex:
        test.fail("unexpected exception happen: %s" % str(ex))
    else:
        if backend_device == "https_cdrom_backend":
            check_https_cdrom_device_mounted(vm, test)
        elif backend_device == "iso_cdrom_backend":
            check_iso_cdrom_device_updated(vm, params, test)
        elif backend_device == "twice_iso_cdrom_backend":
            check_twice_iso_cdrom_device_updated(vm, device_obj, params, test)
        elif backend_device == "requisite_startuppolicy_cdrom_backend":
            check_requisite_startuppolicy_cdrom(vm, params, test)
        elif backend_device == "open_tray_cdrom_backend":
            check_open_tray_cdrom(vm, params, test)
        elif backend_device == "change_startuppolicy_cdrom_backend":
            check_change_startuppolicy_cdrom_backend(vm, params, device_obj, test)
        elif backend_device == "libvirtd_not_crash_on_domstats":
            check_libvirtd_not_crash_on_domstats(vm, old_pid_of_libvirtd, test)
        elif backend_device == "cdrom_reboot_reset_backend":
            check_cdrom_reboot_reset(vm, params, test)
        elif backend_device == "scsi_cdrom_hot_eject_backend":
            check_scsi_cdrom_hot_eject(vm, params, test)
        elif backend_device == "empty_source_cdrom_backend":
            check_empty_source_cdrom(vm, params, test)
        elif backend_device == "dropped_changed_events_startuppolicy":
            check_dropped_changed_events_startuppolicy_backend(vm, params, virsh_session, test)
        elif backend_device == "block_cdrom_tainted":
            check_block_cdrom_log(vm, params, test)
        elif backend_device == "disconnect_audit_cdrom_backend":
            check_disconnect_audit_cdrom(params, test)
    finally:
        # Recover VM.
        if vm.is_alive():
            vm.destroy(gracefully=False)
        LOG.info("Restoring vm...")
        vmxml_backup.sync()
        # Clean up images
        for file_path in CLEANUP_FILES:
            if os.path.exists(file_path):
                os.remove(file_path)
        if backend_device in ["change_startuppolicy_cdrom_backend", "block_cdrom_tainted"]:
            # unload scsi_debug module if loaded
            def _unload():
                linux_modules.unload_module("scsi_debug")
                return True
            utils_misc.wait_for(_unload, timeout=20, ignore_errors=True)
        if backend_device in ["disconnect_audit_cdrom_backend"]:
            restore_libvirtd_config()
        if backend_device == "block_lun_source":
            process.run("losetup -D", shell=True)
