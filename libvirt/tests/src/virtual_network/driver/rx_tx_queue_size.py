import json
import logging

from avocado.utils import process
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
    Start vm with rx_queue_size and tx_queue_size setting

    """
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)
    iface_attrs = eval(params.get('iface_attrs', '{}'))
    status_error = 'yes' == params.get('status_error', 'no')

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        mac = vm_xml.VMXML.get_first_mac_by_name(vm_name)
        iface_attrs['mac_address'] = mac

        vmxml.del_device('interface', by_tag=True)
        libvirt_vmxml.modify_vm_device(
            vmxml, 'interface', iface_attrs, sync_vm=False)
        LOG.debug(f'Modify vmxml to:\n{vmxml}')

        if status_error:
            if params.get('fail_operation') == 'define':
                virsh_result = virsh.define(vmxml.xml, debug=True)
            else:
                vmxml.sync()
                virsh_result = virsh.start(vm_name, debug=True)
            libvirt.check_exit_status(virsh_result, status_error)
            return

        vmxml.sync()
        vm.start()
        LOG.debug(f'VMXML of {vm_name}:\n{virsh.dumpxml(vm_name).stdout_text}')
        vm_sess = vm.wait_for_serial_login()

        iface = network_base.get_iface_xml_inst(vm_name, 'after vm start')
        actual_rx = int(iface.driver.driver_attr.get('rx_queue_size'))
        actual_tx = int(iface.driver.driver_attr.get('tx_queue_size'))
        LOG.debug(
            f'Actual rx_queue_size={actual_rx} tx_queue_size={actual_tx}')
        if str(actual_rx) != iface_attrs['driver']['driver_attr']['rx_queue_size']:
            test.fail(f"Wrong rx_queue_size {actual_rx}, should be "
                      f"{iface_attrs['driver']['driver_attr']['rx_queue_size']}")
        if actual_tx != 256:
            test.fail(f'Wrong tx_queue_size {actual_tx}, should be 256')

        process.run('ps -ef|grep qemu', shell=True)

        vm_iface = utils_net.get_linux_iface_info(
            mac=mac, session=vm_sess)['ifname']

        eth_output = vm_sess.cmd(f'ethtool -g {vm_iface}')
        LOG.debug(eth_output)
        eth_output_json = vm_sess.cmd_output(f'ethtool --json -g {vm_iface}')
        LOG.debug(eth_output_json)
        eth_info = json.loads(eth_output_json)[0]
        if eth_info['rx-max'] != actual_rx or eth_info['rx'] != actual_rx:
            test.fail('Wrong rx from vm, please check log')
        if eth_info['tx-max'] != actual_tx or eth_info['tx'] != actual_tx:
            test.fail('Wrong tx from vm, please check log')

        ips = {'outside_ip': params.get('outside_ip')}
        network_base.ping_check(params, ips, vm_sess)
        vm_sess.close()

    finally:
        bkxml.sync()
