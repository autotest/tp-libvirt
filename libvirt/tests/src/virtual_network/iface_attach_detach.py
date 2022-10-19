import logging

from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices import interface
from virttest.utils_test import libvirt

LOG = logging.getLogger('avocado.' + __name__)
VIRSH_ARGS = {'debug': True, 'ignore_status': False}


def modify_iface(vm_name, index=0, **attrs):
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    xml_devices = vmxml.devices

    try:
        iface_index = xml_devices.index(
            xml_devices.by_device_tag("interface")[index])
        iface = xml_devices[iface_index]
    except IndexError:
        iface = interface.Interface()

    iface.setup_attrs(**attrs)
    LOG.debug('iface after modification: %s', iface)

    return iface


def run(test, params, env):
    """
    Test attaching/detaching interface
    """
    def test_boot_order():
        pre_vm_state = params.get('pre_vm_state', '')
        iface_pre_at = modify_iface(vm_name, **iface_attrs)

        if scenario == 'with_boot_disk':
            libvirt.change_boot_order(vm_name, 'disk', '1')
        if pre_vm_state == 'hot':
            vm.start()

        LOG.debug('vm xml is :%s\n', virsh.dumpxml(vm_name).stdout_text)
        test_result = virsh.attach_device(vm_name, iface_pre_at.xml,
                                          flagstr=virsh_options, debug=True)
        libvirt.check_exit_status(test_result, status_error)
        libvirt.check_result(test_result, expected_fails=error_msg)

    # Variables assignment
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)

    case = params.get('case', '')
    scenario = params.get('scenario', '')
    status_error = 'yes' == params.get('status_error', 'no')
    error_msg = params.get('error_msg', '')
    bk_vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    iface_attrs = eval(params.get('iface_attrs', '{}'))
    virsh_options = params.get('virsh_options', '')
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)

    # Get test function
    test_func = eval('test_%s' % case)

    try:
        test_func()

    finally:
        bk_vmxml.sync()
