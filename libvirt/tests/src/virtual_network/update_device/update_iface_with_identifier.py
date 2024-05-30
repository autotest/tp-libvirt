import logging

from virttest import utils_misc
from virttest import utils_net
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.virtual_network import network_base

VIRSH_ARGS = {'ignore_status': False, 'debug': True}

LOG = logging.getLogger('avocado.' + __name__)


def check_cmd_result(cmd_result, status_error, err_msg=None):
    """
    Check command result including exit status and error message

    :param cmd_result: command result instance
    :param status_error: expect error of command
    :param err_msg: error message of command, defaults to None
    """
    libvirt.check_exit_status(cmd_result, status_error)
    if err_msg:
        libvirt.check_result(cmd_result, err_msg)


def run(test, params, env):
    """
    Verify updating interface can identify by each of the mac/alias/PCI address,
    and also by them together.
    """
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)
    status_error = 'yes' == params.get('status_error', 'no')
    err_msg = params.get('err_msg')
    alias_a = 'ua-' + utils_misc.generate_random_string(6)
    alias_b = 'ua-' + utils_misc.generate_random_string(6)
    rand_mac = utils_net.generate_mac_address_simple()
    rand_alias = 'randa-' + utils_misc.generate_random_string(6)
    iface_a_attrs = eval(params.get('iface_a_attrs', '{}'))
    iface_b_attrs = eval(params.get('iface_b_attrs', '{}'))
    update_attrs = eval(params.get('update_attrs', '{}'))
    update_pci = 'yes' == params.get('update_pci', 'no')
    del_tags = eval(params.get('del_tags', '[]'))
    operation = params.get('operation')

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        vmxml.del_device('interface', by_tag=True)
        for attrs in (iface_a_attrs, iface_b_attrs):
            iface_x = libvirt_vmxml.create_vm_device_by_type(
                'interface', attrs)
            libvirt.add_vm_device(vmxml, iface_x)
        LOG.debug(f'VMXMLof {vm_name}:\n{virsh.dumpxml(vm_name).stdout_text}')

        vm.start()

        # Get target iface
        iface = network_base.get_iface_xml_inst(vm_name, '1st on vm')
        mac = iface.mac_address

        LOG.debug(f'Update iface with attrs: {update_attrs}')
        iface.setup_attrs(**update_attrs)

        if update_pci:
            LOG.debug('Update iface xml pci address')
            pci_addr_attrs = iface.address['attrs']
            pci_addr_attrs['slot'] = str(hex(int(pci_addr_attrs['slot'],
                                                 16) + 3))
            iface.setup_attrs(address={'attrs': pci_addr_attrs})
            LOG.debug(f'Update iface pci address to: {iface.address}')

        for tag in del_tags:
            LOG.debug(f'Remove <{tag}> from iface xml.')
            iface.xmltreefile.remove_by_xpath(tag)
        iface.xmltreefile.write()
        LOG.debug(f'Update iface with xml:\n{iface}')

        if operation == 'update':
            up_result = virsh.update_device(vm_name, iface.xml, debug=True)
            check_cmd_result(up_result, status_error, err_msg)
            if status_error:
                return
            iface_update = network_base.get_iface_xml_inst(vm_name,
                                                           'after update')
            if iface_update.link_state == update_attrs['link_state']:
                LOG.info('link_state of interface after update check PASS')
            else:
                test.fail(f'Interface link_state after update should be '
                          f'{update_attrs["link_state"]}')

        elif operation == 'hotunplug':
            dt_result = virsh.detach_device(vm_name, iface.xml,
                                            wait_for_event=True,
                                            event_timeout=20,
                                            debug=True)
            check_cmd_result(dt_result, status_error, err_msg)
            if status_error:
                return
            iflist = virsh.domiflist(vm_name, debug=True).stdout_text
            if mac not in iflist:
                LOG.info('Interface successfully detached:checked by domiflist')
            else:
                test.fail(f'Interface with mac {mac} not detached')
        else:
            test.error(f'Unknown operation: {operation}')

    finally:
        bkxml.sync()
