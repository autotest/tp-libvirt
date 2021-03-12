import logging
import os
import re

from virttest import utils_hotplug
from virttest import utils_sys
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_cpu
from virttest.utils_test import libvirt


def pretest_vm_setup(params, case):
    """
    Setup vm before test

    :param params: test params
    :param case: test case
    """
    vm_name = params.get('main_vm', '')
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    if case == 'report_failure':
        libvirt_cpu.add_cpu_settings(vmxml, params)
        logging.debug('Updated cpu xml: %s', vmxml.cpu)


def pretest_log_setup(params):
    """
    Setup log before test

    :param params: test params
    :return: log conf object
    """
    libvirtd_conf_dict = eval(params.get("libvirtd_conf_dict", '{}'))
    libvirtd_conf = libvirt.customize_libvirt_config(libvirtd_conf_dict)
    return libvirtd_conf


def clear_log(log_path):
    """
    Remove log file. Clear old log to remove influence

    :param log_path: path of log file
    """
    if os.path.exists(log_path):
        logging.debug("Clear log file '%s'", log_path)
        os.remove(log_path)


def start_vm(vm):
    """
    Start vm and wait for successful login

    :param vm: vm to be started
    """
    vm.start()
    vm.wait_for_login().close()


def create_mem_device(params):
    """
    Create memory device/xml with given params

    :param params: test params
    :return: memory device object being created
    """
    mem_device_params = {
        k.replace('mem_device_', ''): v
        for k, v in params.items() if k.startswith('mem_device_')
    }
    return utils_hotplug.create_mem_xml(**mem_device_params)


def check_event(target_event, event_output):
    """
    Check whether target_event exists in actual event_output

    :param target_event: event that's supposed to exist
    :param event_output: actual event output
    :return: True if target_event exists, False if not.
    """
    if re.search(target_event, event_output):
        logging.debug('event found: %s', target_event)
        return True
    else:
        logging.error('event not found, %s', target_event)
        return False


def run(test, params, env):
    """
    Test memory hotplug
    """
    case = params.get('case', '')
    vm_name = params.get('main_vm', '')
    log_path = params.get('log_path', '/var/log/libvirt/libvirt_daemons.log')

    vm = env.get_vm(vm_name)
    bkxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    virsh_args = {'ignore_status': False, 'debug': True}

    def test_report_failure():
        """
        Execute test case of 'report failure'
        """
        virsh_session = virsh.EventTracker.start_get_event(vm_name)
        mem_xml = create_mem_device(params)
        start_vm(vm)
        virsh.attach_device(vm_name, mem_xml.xml, **virsh_args)
        libvirt.check_logfile('"execute":"device_add"', log_path, True)
        detach_result = virsh.detach_device(vm_name, mem_xml.xml, debug=True)
        libvirt.check_exit_status(detach_result, True)
        libvirt.check_logfile('"execute":"device_del"', log_path, True)
        libvirt.check_logfile('"event": "ACPI_DEVICE_OST"', log_path, True)
        event_output = virsh.EventTracker.finish_get_event(virsh_session)
        event_type = 'device-removal-failed'
        if not check_event(event_type, event_output):
            test.fail('Event checking of %s failed.' % event_type)
        session = vm.wait_for_login()
        dmesg_pattern = 'Offline failed'
        if not utils_sys.check_dmesg_output(dmesg_pattern, True, session):
            test.fail('Dmesg check for %s failed' % dmesg_pattern)

    try:
        clear_log(log_path)
        config = pretest_log_setup(params)
        pretest_vm_setup(params, case)

        # Test steps of cases
        if case == 'report_failure':
            test_report_failure()

    finally:
        bkxml.sync()
        config.restore()
