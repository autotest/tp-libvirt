import logging

from virttest import utils_net
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.virtual_network import network_base

VIRSH_ARGS = {'ignore_status': False, 'debug': True}

LOG = logging.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test Update an interface by update-device
    with --live/--config/--current/--persistent on active/inactive domain
    """
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)
    status_error = 'yes' == params.get('status_error', 'no')
    err_msg = params.get('err_msg')
    iface_attrs = eval(params.get('iface_attrs', '{}'))
    update_attrs = eval(params.get('update_attrs', '{}'))
    update_expect = eval(params.get('update_expect', '{}'))
    options = params.get('options')
    vm_active = eval(params.get('vm_active'))

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        libvirt_vmxml.modify_vm_device(vmxml, 'interface', iface_attrs)
        LOG.debug(f'VMXML of {vm_name}:\n{virsh.dumpxml(vm_name).stdout_text}')

        if vm_active:
            vm.start()
            vm.wait_for_serial_login().close()

        iface = network_base.get_iface_xml_inst(vm_name, 'on vm')
        mac = iface.mac_address
        LOG.debug(f'Mac address of iface: {mac}')
        LOG.debug(f'Update iface with attrs: {update_attrs}')
        iface.setup_attrs(**update_attrs)

        # Generate options for update-device command
        options = options.split('_') if 'none' not in options else []
        options = ' '.join([f'--{opt}' for opt in options])
        LOG.debug(f'Update iface with xml:\n{iface}')
        up_result = virsh.update_device(vm_name, iface.xml, flagstr=options,
                                        debug=True)
        libvirt.check_exit_status(up_result, status_error)
        if err_msg:
            libvirt.check_result(up_result, err_msg)

        if vm_active:
            iface_active = network_base.get_iface_xml_inst(
                vm_name, 'on active vm after update')
            LOG.debug(f'link state on active xml should be '
                      f'{"down" if update_expect["active"] else "up"}')
            if (iface_active.link_state == 'down') is not update_expect['active']:
                test.fail('update result of iface xml on active vm '
                          'not met expectation.')

        iface_inactive = network_base.get_iface_xml_inst(
            vm_name, 'on inactive vm after update', options='--inactive')
        LOG.debug(f'link state on inactive xml should be '
                  f'{"down" if update_expect["inactive"] else "up"}')
        if (iface_inactive.link_state == 'down') is not update_expect['inactive']:
            test.fail('update result of iface xml on inactive vm '
                      'not met expectation.')

        # Check update result on vm with ethtool
        if vm_active:
            session = vm.wait_for_serial_login()
            vm_iface_info = utils_net.get_linux_iface_info(
                mac=mac, session=session)
            LOG.debug(f'iface info on vm: {vm_iface_info}')
            ethtool_output = session.cmd_output(
                f'ethtool {vm_iface_info["ifname"]}')
            LOG.debug(f'ethtool output:\n{ethtool_output}')
            LOG.debug(f'"Link detected" should be '
                      f'{"no" if update_expect["active"] else "yes"}')
            if ('Link detected: no' in ethtool_output) is not \
                    update_expect['active']:
                test.fail('ethtool check inside vm failed.')
    finally:
        bkxml.sync()
