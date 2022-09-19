import logging as log

from avocado.utils import process

from virttest import libvirt_version
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

LOG = log.getLogger('avocado.' + __name__)

VIRSH_ARGS = {'debug': True, 'ignore_status': False}


def run(test, params, env):
    """
    Test label
    """

    def run_test_unix_socket():
        """
        Test steps of unix socket case
        """
        # Setup serial device and channel device and add them to vmxml
        serial_attrs = eval(params.get('serial_attrs', '{}'))
        channel_attrs = eval(params.get('channel_attrs', '{}'))
        LOG.debug('Serial device attrs: %s', serial_attrs)
        serial_device = libvirt_vmxml.create_vm_device_by_type(
            'serial', serial_attrs)
        vmxml.add_device(serial_device)
        LOG.debug('Channel device attrs: %s', channel_attrs)
        channel_device = libvirt_vmxml.create_vm_device_by_type(
            'channel', channel_attrs)
        vmxml.add_device(channel_device)
        vmxml.sync()
        LOG.debug(virsh.dumpxml(vm_name).stdout_text)

        # Start vm and check the label of the unix sockets
        vm.start()
        for device in ['serial', 'channel']:
            path = params.get('%s_path' % device)
            label = params.get('%s_label' % device)
            label_output = process.run('ls -lZ %s' % path, shell=True)
            libvirt.check_result(label_output, expected_match=label)

    # Version check for current test
    libvirt_version.is_libvirt_feature_supported(params)

    # Variable assignment
    case = params.get('case', '')

    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)

    status_error = "yes" == params.get('status_error', 'no')
    bkxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)

    # Get runtest function
    run_test = eval('run_test_%s' % case)

    try:
        # Execute test
        run_test()

    finally:
        bkxml.sync()
