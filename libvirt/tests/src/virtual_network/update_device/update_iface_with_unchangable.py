import logging

from virttest import utils_net
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml

from provider.virtual_network import network_base

VIRSH_ARGS = {'ignore_status': False, 'debug': True}

LOG = logging.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    To test that libvirt can compare current xml and the xml to be updated correctly.
    keep all the attributes which do not support live update in the xml unchanged,
    also with <link state> that can be live updated changed, try to update it. It should succeed.
    """
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        # Setup boot config of vmxml
        osxml = vmxml.os
        osxml.del_boots()
        vmxml.os = osxml
        libvirt_vmxml.modify_vm_device(vmxml, 'disk', {'boot': '1'})

        vmxml.del_device('interface', by_tag=True)

        iface_attrs = {k.replace('iface_attrs_', ''): v
                       if v[0].isalnum() else eval(v)
                       for k, v in params.items()
                       if k.startswith('iface_attrs_')}

        libvirt_vmxml.modify_vm_device(vmxml, 'interface', iface_attrs)
        LOG.debug(f'VMXML of {vm_name}:\n{virsh.dumpxml(vm_name).stdout_text}')

        vm.start()

        iface = network_base.get_iface_xml_inst(vm_name, 'on vm')
        mac = iface.mac_address
        update_attrs = eval(params.get('update_attrs', '{}'))
        LOG.debug(f'Update iface with attrs: {update_attrs}')
        iface.setup_attrs(**update_attrs)
        LOG.debug(f'Update iface with xml:\n{iface}')

        virsh.update_device(vm_name, iface.xml, **VIRSH_ARGS)

        iface_update = network_base.get_iface_xml_inst(vm_name, 'after update')
        LOG.debug(f'link state after update: {iface_update.link_state}')
        if iface_update.link_state != 'down':
            test.fail('Link state of interface should be down after update')

        # Check update result on vm with ethtool
        session = vm.wait_for_serial_login()
        vm_iface_info = utils_net.get_linux_iface_info(
            mac=mac, session=session)
        LOG.debug(f'iface info on vm: {vm_iface_info}')
        ethtool_output = session.cmd_output(
            f'ethtool {vm_iface_info["ifname"]}')
        LOG.debug(f'ethtool output:\n{ethtool_output}')
        LOG.debug(f'"Link detected" should be no')
        if 'Link detected: no' not in ethtool_output:
            test.fail('ethtool check inside vm failed.')

    finally:
        bkxml.sync()
