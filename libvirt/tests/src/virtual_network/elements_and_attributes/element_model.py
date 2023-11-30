import logging

from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.virtual_network import network_base
from provider.interface import interface_base

VIRSH_ARGS = {'ignore_status': False, 'debug': True}

LOG = logging.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test 'model' element of interface
    """
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)
    outside_ip = params.get('outside_ip')
    status_error = 'yes' == params.get('status_error', 'no')
    err_msg = params.get('err_msg')
    pci_model = params.get('pci_model')
    iface_attrs = eval(params.get('iface_attrs', '{}'))
    iface_driver = params.get('iface_driver')
    model_type = params.get('model_type')
    if model_type == 'default':
        iface_attrs.pop('model')

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        vmxml.del_device('interface', by_tag=True)
        libvirt_vmxml.modify_vm_device(vmxml, 'interface', iface_attrs)

        LOG.debug(f'VMXML of {vm_name}:\n{virsh.dumpxml(vm_name).stdout_text}')

        if status_error:
            start_result = virsh.start(vm_name, debug=True)
            libvirt.check_exit_status(start_result, status_error)
            if err_msg:
                libvirt.check_result(start_result, expected_fails=err_msg)
            return

        vm.start()
        virsh.domiflist(vm_name, **VIRSH_ARGS)
        iflist = libvirt.get_interface_details(vm_name)
        LOG.debug(f'iflist of vm: {iflist}')
        iface_info = iflist[0]
        model_type = 'rtl8139' if model_type == 'default' else model_type
        if iface_info['model'] == model_type:
            LOG.debug('Model check of domiflist: PASS')
        else:
            test.fail(f'Expect interface model {model_type}, '
                      f'but got {iface_info["model"]}')

        session = vm.wait_for_serial_login()
        vm_iface = interface_base.get_vm_iface(session)

        eth_output = session.cmd_output(f'ethtool -i {vm_iface} | grep driver')
        LOG.debug(eth_output)
        msg = f'Found expected driver "{iface_driver}" in ethtool output'
        if iface_driver in eth_output:
            LOG.debug(msg)
        else:
            test.fail('Not ' + msg)

        ips = {'outside_ip': outside_ip}
        network_base.ping_check(params, ips, session, force_ipv4=True)
        session.close()

        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        iface = vmxml.get_devices('interface')[0]
        LOG.debug(f'Interface xml after vm started:\n{iface}')
        ctrl_index = int(iface.fetch_attrs()['address']['attrs']['bus'], 16)
        controllers = vmxml.get_devices('controller')
        iface_controller = [c for c in controllers if c.type == 'pci' and
                            c.index == str(ctrl_index)][0]
        LOG.debug(f'Controller xml:\n{iface_controller}')

        if iface_controller.model == pci_model:
            LOG.debug('XML controller model check: PASS')
        else:
            test.fail(f'Expect pci model: {pci_model}, '
                      f'and got {iface_controller.model}')
    finally:
        bkxml.sync()
