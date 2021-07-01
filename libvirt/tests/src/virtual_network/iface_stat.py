import datetime
import logging
import re

from virttest import utils_net
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


def get_host_iface_stat(vm_name, iface):
    """
    Get iface stat on host

    :param vm_name: vm with the iface
    :param iface: target iface to get stat of
    :return: iface stat
    """
    logging.debug('Start at %s', datetime.datetime.now())
    stat_host = virsh.domifstat(vm_name, iface, debug=True).stdout
    iface_info = stat_host.splitlines()
    iface_info = [line.replace(iface, '').strip()
                  for line in iface_info if iface in line]
    output = {line.split()[0]: int(line.split()[1]) for line in iface_info}
    logging.info('Host iface stat: %s', output)

    return output


def get_vm_iface_stat(session, iface):
    """
    Get iface stat in vm

    :param session: session of vm
    :param iface: target iface
    :return: iface stat in vm
    """
    logging.debug('Start at %s', datetime.datetime.now())
    stat_vm = session.cmd('ifconfig %s' % iface)
    logging.debug('Iface ifconfig in vm: %s', stat_vm)

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

    logging.info('Iface stat in vm: %s', output)
    return output


def compare_iface_stat(vm_stat, host_stat, bar=0.2):
    """
    Compare iface stat between host and vm

    :param vm_stat: iface stat in vm
    :param host_stat: iface stat on host
    :param bar: allowable error
    :return: True if the two match within allowable eror, False if not
    """

    flag = True
    if set(vm_stat.keys()) != set(host_stat.keys()):
        logging.error('Iface info is not complete on host/vm.')
        return False

    for key in vm_stat.keys():
        delta = abs(vm_stat[key] - host_stat[key])
        if delta == 0:
            continue
        elif delta / max(vm_stat[key], host_stat[key]) > bar:
            logging.error('Value of %s on host and vm are not close.', key)
            flag = False
    return flag


def run(test, params, env):
    """
    Test iface stat
    """
    vm_name = params.get('main_vm')
    case = params.get('case', '')
    vm = env.get_vm(vm_name)

    bk_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    try:
        if case == 'compare':
            host_ifname = utils_net.get_net_if(state="UP")[0]
            iface_dict = {k.replace('new_iface_', ''): v
                          for k, v in params.items() if k.startswith('new_iface_')}
            iface_dict['source'] = iface_dict['source'] % host_ifname

            logging.debug('iface_dict is %s', iface_dict)
            libvirt.modify_vm_iface(vm_name, 'update_iface', iface_dict)
            vm.start()

            vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
            iface = vmxml.devices.by_device_tag("interface")[0]
            iface_mac = iface.mac_address
            iface_target_dev = iface.target['dev']

            session = vm.wait_for_serial_login(timeout=60)
            iface_in_vm = utils_net.get_linux_ifname(session, iface_mac)

            session.cmd('ping www.redhat.com -4 -c 20')
            host_iface_stat = get_host_iface_stat(vm_name, iface_target_dev)
            vm_iface_stat = get_vm_iface_stat(session, iface_in_vm)
            session.close()

            if not compare_iface_stat(vm_iface_stat, host_iface_stat, bar=0.2):
                test.fail('Iface stat of host and vm should be close.')

    finally:
        bk_xml.sync()
