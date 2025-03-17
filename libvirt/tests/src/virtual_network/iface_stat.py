import datetime
import logging
import re

from avocado.utils import process

from virttest import utils_misc
from virttest import utils_net
from virttest import virsh
from virttest import libvirt_version

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.virtual_network import network_base

LOG = logging.getLogger('avocado.' + __name__)


def get_host_iface_stat(vm_name, iface, **virsh_args):
    """
    Get iface stat on host

    :param vm_name: vm with the iface
    :param iface: target iface to get stat of
    :return: iface stat
    """
    LOG.debug('Start at %s', datetime.datetime.now())
    stat_host = virsh.domifstat(vm_name, iface, debug=True, **virsh_args).stdout
    iface_info = stat_host.splitlines()
    iface_info = [line.replace(iface, '').strip()
                  for line in iface_info if iface in line]
    output = {line.split()[0]: int(line.split()[1]) for line in iface_info}
    LOG.info('Host iface stat: %s', output)

    return output


def get_vm_iface_stat(session, iface):
    """
    Get iface stat in vm

    :param session: session of vm
    :param iface: target iface
    :return: iface stat in vm
    """
    LOG.debug('Start at %s', datetime.datetime.now())
    stat_vm = session.cmd('ifconfig %s' % iface)
    LOG.debug('Iface ifconfig in vm: %s', stat_vm)

    def _get_info(pattern):
        """
        Format output of search result

        :param pattern: pattern to be searched for
        :return: formatted output
        """
        search_result = re.search(pattern, stat_vm)
        if search_result:
            return int(search_result.group(1)), int(search_result.group(2))
        else:
            return None, None

    output = dict()
    output['rx_packets'], output['rx_bytes'] = _get_info(
        'RX packets (\d+)\s+bytes (\d+)')
    output['rx_errs'], output['rx_drop'] = _get_info(
        'RX errors (\d+)\s+dropped (\d+)')
    output['tx_packets'], output['tx_bytes'] = _get_info(
        'TX packets (\d+)\s+bytes (\d+)')
    output['tx_errs'], output['tx_drop'] = _get_info(
        'TX errors (\d+)\s+dropped (\d+)')

    LOG.info('Iface stat in vm: %s', output)
    return output


def compare_iface_stat(vm_stat, host_stat, bar=0.1):
    """
    Compare iface stat between host and vm

    :param vm_stat: iface stat in vm
    :param host_stat: iface stat on host
    :param bar: allowable error
    :return: True if the two match within allowable error, False if not
    """

    flag = True
    if set(vm_stat.keys()) != set(host_stat.keys()):
        LOG.error('Iface info is not complete on host/vm.')
        return False

    for key in vm_stat.keys():
        delta = abs(vm_stat[key] - host_stat[key])
        if key == 'rx_errs' or key == 'rx_drop':
            continue
        if delta == 0:
            continue
        else:
            diff_ratio = delta / max(vm_stat[key], host_stat[key])
            LOG.debug(f'Difference ratio of {key} is {diff_ratio}')
            if diff_ratio > bar:
                LOG.error('Value of %s on vm (%s) and host (%s) are not close.',
                          key, vm_stat[key], host_stat[key])
                flag = False
    return flag


def run(test, params, env):
    """
    Test iface stat
    """
    libvirt_version.is_libvirt_feature_supported(params)
    vm_name = params.get('main_vm')
    case = params.get('case', '')
    scenario = params.get('scenario', '')
    vm = env.get_vm(vm_name)
    iface_type = params.get('iface_type', '')
    rand_id = utils_misc.generate_random_string(3)
    bridge_name = 'br_' + rand_id
    device_type = params.get('device_type', '')
    tap_name = device_type + '_' + rand_id
    iface_attrs = eval(params.get('iface_attrs', '{}'))
    unpr_user = 'unpr_user_' + rand_id if params.get('unpr_user') else ''
    username = params.get('username')
    password = params.get('password')
    mac_addr = utils_net.generate_mac_address_simple()
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bk_xml = vmxml.copy()

    if unpr_user:
        LOG.info(f'Create unprivileged user {unpr_user}')
        process.run(f'useradd {unpr_user}', shell=True)

    try:
        if case == 'compare':

            def _collect_and_compare_stat(vm_name, session, **virsh_args):
                for _ in range(3):
                    session.cmd('curl -O https://avocado-project.org/data/'
                                'assets/jeos/27/jeos-27-64.qcow2.xz -L',
                                timeout=60, ignore_all_errors=True)
                    session.sendcontrol("c")
                host_iface_stat = get_host_iface_stat(vm_name,
                                                      iface_target_dev,
                                                      **virsh_args)
                vm_iface_stat = get_vm_iface_stat(session, iface_in_vm)
                if not compare_iface_stat(vm_iface_stat, host_iface_stat,
                                          bar=0.1):
                    test.fail('Iface stat of host and vm should be close.')

            host_iface = params.get("host_iface")
            if not host_iface:
                host_iface = utils_net.get_default_gateway(
                    iface_name=True, force_dhcp=True, json=True)
            if iface_type == 'direct':
                iface_dict = {k.replace('new_iface_', ''): v
                              for k, v in params.items()
                              if k.startswith('new_iface_')}
                iface_dict['source'] = iface_dict['source'] % host_iface

                LOG.debug('iface_dict is %s', iface_dict)
                libvirt.modify_vm_iface(vm_name, 'update_iface', iface_dict)
                vm.start()

                vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
                iface = vmxml.devices.by_device_tag("interface")[0]
                iface_mac = iface.mac_address
                iface_target_dev = iface.target['dev']

                session = vm.wait_for_serial_login(timeout=60)
                iface_in_vm = utils_net.get_linux_ifname(session, iface_mac)
                _collect_and_compare_stat(vm_name, session)
                session.close()

            elif iface_type == 'ethernet':
                if device_type == 'tap':
                    utils_net.create_linux_bridge_tmux(bridge_name, host_iface)
                    network_base.create_tap(tap_name, bridge_name, unpr_user)
                elif device_type == 'macvtap':
                    mac_addr = network_base.create_macvtap(tap_name, host_iface,
                                                           unpr_user)
                iface_attrs['mac_address'] = mac_addr

                unpr_vmxml = network_base.prepare_vmxml_for_unprivileged_user(
                    unpr_user, vmxml)
                unpr_vmxml.del_device('interface', by_tag=True)
                libvirt_vmxml.modify_vm_device(unpr_vmxml, 'interface',
                                               iface_attrs)
                unpr_vmxml.del_seclabel(by_attr=[('model', 'dac')])
                network_base.define_vm_for_unprivileged_user(unpr_user,
                                                             unpr_vmxml)
                unpr_vm_name = unpr_vmxml.vm_name
                virsh.start(unpr_vm_name, debug=True, ignore_status=False,
                            unprivileged_user=unpr_user)
                session = network_base.unprivileged_user_login(unpr_vm_name,
                                                               unpr_user,
                                                               username,
                                                               password)
                iface = unpr_vmxml.devices.by_device_tag("interface")[0]
                iface_mac = iface.mac_address
                iface_in_vm = utils_net.get_linux_ifname(session, iface_mac)
                iface_target_dev = iface.target['dev']
                _collect_and_compare_stat(unpr_vm_name, session,
                                          unprivileged_user=unpr_user)
                session.close()

    finally:
        bk_xml.sync()
        if 'tap' in device_type:
            virsh.destroy(unpr_vm_name, unprivileged_user=unpr_user, debug=True)
            virsh.undefine(unpr_vm_name, options='--nvram',
                           unprivileged_user=unpr_user, debug=True)
            network_base.delete_tap(tap_name)
            if device_type == 'tap':
                utils_net.delete_linux_bridge_tmux(bridge_name, host_iface)
        if unpr_user:
            process.run(f'pkill -u {unpr_user};userdel -f -r {unpr_user}',
                        shell=True, ignore_status=True)
