import logging

import aexpect
from virttest import libvirt_version
from virttest import remote
from virttest import utils_misc
from virttest import utils_net
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_unprivileged

from provider.virtual_network import network_base

LOG = logging.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test interface with unprivileged user
    """

    if not libvirt_version.version_compare(5, 6, 0):
        test.cancel('Libvirt version is too low for this test.')

    vm_name = params.get('unpr_vm_name')
    test_user = params.get('test_user', '')
    test_passwd = params.get('test_passwd', '')
    unpr_vm_args = {
        'username': params.get('username'),
        'password': params.get('password'),
    }
    vm = libvirt_unprivileged.get_unprivileged_vm(vm_name, test_user,
                                                  test_passwd,
                                                  **unpr_vm_args)
    uri = f'qemu+ssh://{test_user}@localhost/session'
    virsh_ins = virsh.Virsh(uri=uri)
    host_session = aexpect.ShellSession('su')
    remote.VMManager.set_ssh_auth(host_session, 'localhost', test_user,
                                  test_passwd)
    host_session.close()

    rand_id = '_' + utils_misc.generate_random_string(3)
    bridge_name = params.get('bridge_name', 'test_br0') + rand_id
    device_type = params.get('device_type', '')
    host_iface = params.get('host_iface')
    if not host_iface:
        host_iface = utils_net.get_default_gateway(
            iface_name=True, force_dhcp=True).split()[0]
    tap_name = params.get('tap_name', 'mytap0') + rand_id
    macvtap_name = params.get('macvtap_name', 'mymacvtap0') + rand_id
    case = params.get('case', '')

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name,
                                                   virsh_instance=virsh_ins)
    bkxml = vmxml.copy()

    try:

        if case == 'precreated':
            if device_type == 'tap':
                # Create bridge
                iface_attrs = network_base.get_iface_xml_inst(
                    vm_name, 'on vm', virsh_ins=virsh_ins).fetch_attrs()
                if iface_attrs.get('driver', {}).get('driver_attr', {}).get('queues'):
                    tap_flag = 'multi_queue'
                else:
                    tap_flag = ''
                utils_net.create_linux_bridge_tmux(bridge_name, host_iface)

                network_base.create_tap(
                    tap_name, bridge_name, test_user, tap_flag)

            if device_type == 'macvtap':
                # Create macvtap device
                mac_addr = network_base.create_macvtap(
                    macvtap_name, host_iface, test_user)

            # Modify interface
            all_devices = vmxml.devices
            iface_list = all_devices.by_device_tag('interface')
            if not iface_list:
                test.error('No iface to modify')
            iface = iface_list[0]

            # Remove other interfaces
            for ifc in iface_list[1:]:
                all_devices.remove(ifc)

            if device_type == 'tap':
                dev_name = tap_name
            elif device_type == 'macvtap':
                dev_name = macvtap_name
            else:
                test.error('Invalid device type: {}'.format(device_type))

            if_index = all_devices.index(iface)
            iface = all_devices[if_index]
            iface.type_name = 'ethernet'
            iface.target = {
                'dev': dev_name,
                'managed': 'no'
            }

            if device_type == 'macvtap':
                iface.mac_address = mac_addr
            LOG.debug(iface)

            vmxml.devices = all_devices
            LOG.debug(vmxml)

            vm.start()
            session = vm.wait_for_serial_login(60)
            LOG.debug(session.cmd_output('ifconfig'))
            ips = {'outside_ip': params.get('outside_ip')}
            network_base.ping_check(params, ips, session)
            session.close()

    finally:
        bkxml.sync(virsh_instance=virsh_ins)
        if case == 'precreated':
            try:
                if device_type in ('tap', 'macvtap'):
                    network_base.delete_tap(tap_name)
            except Exception:
                pass
            finally:
                utils_net.delete_linux_bridge_tmux(bridge_name, host_iface)
