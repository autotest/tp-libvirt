import logging
import platform

from avocado.utils import process

from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Test Guest can start with different cpu mode settings
    and do managedsave
    """
    if 'ppc64le' not in platform.machine().lower():
        test.cancel('This case is for ppc only')

    vm_name = params.get('main_vm')
    status_error = 'yes' == params.get('status_error', 'no')
    error_msg = params.get('error_msg', '')

    cpu_mode = params.get('cpu_mode', '')
    match = params.get('match', '')
    model = params.get('model', '')
    fallback = params.get('fallback', '')
    qemu_line = params.get('qemu_line', '')
    do_managedsave = 'yes' == params.get('do_managedsave', 'no')

    bkxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    try:
        # Test mode='custom' and cpu model is the exact model which
        # should be upper case. And qemu command line also should
        # include '-cpu $MODEL'
        model = model.upper() if cpu_mode == 'custom' else model
        qemu_line = '-cpu %s' % model if model.isupper() else qemu_line

        # Create cpu xml for test
        cpu_xml = vm_xml.VMCPUXML()
        cpu_xml.mode = cpu_mode
        if model:
            cpu_xml.model = model
        if fallback:
            cpu_xml.fallback = fallback
        if match:
            cpu_xml.match = match
        logging.debug(cpu_xml)

        # Update vm's cpu
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        vmxml.cpu = cpu_xml
        vmxml.sync()
        logging.debug(vmxml)

        # Check if vm could start successfully
        result = virsh.start(vm_name, debug=True)
        libvirt.check_exit_status(result, status_error)

        # Check error message of negative cases
        if status_error and error_msg:
            if model.lower() == 'power42':
                msg_list = error_msg.split(',')
                error_msg = msg_list[0] if model.isupper() else msg_list[1]
            libvirt.check_result(result, expected_fails=[error_msg])

        # Check qemu command line and other checkpoints
        if not status_error:
            if qemu_line:
                logging.info('Search for "%s" in qemu command line', qemu_line)
                qemu_cmd_line = process.run(
                    'ps -ef|grep qemu', shell=True, verbose=True).stdout_text
                if qemu_line in qemu_cmd_line:
                    logging.info('PASS: qemu command line check')
                else:
                    test.fail('Fail: qemu command line check: %s not found' % qemu_line)
            if do_managedsave:
                managed_save_result = virsh.managedsave(vm_name, debug=True)
                libvirt.check_exit_status(managed_save_result)
                start_result = virsh.start(vm_name, debug=True)
                libvirt.check_exit_status(start_result)

    finally:
        bkxml.sync()
