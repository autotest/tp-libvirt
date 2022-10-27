import os
import re
import logging as log
import time

from avocado.utils import process
from avocado.utils.software_manager.backends import rpm

from virttest import element_tree
from virttest import virsh
from virttest import utils_net
from virttest import utils_libvirtd
from virttest.libvirt_xml import network_xml
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.interface import Interface
from virttest.utils_libvirt import libvirt_virtio
from virttest.utils_test import libvirt


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def check_iface_link(session, mac, stat):
    """
    Check link state of interface inside a vm

    :param session: vm's session
    :param mac: mac address of iface
    :param stat: link state, could be 'up' or 'down'
    :return: True if check passed, False if failed
    """
    stat_map = {'up': 'yes',
                'down': 'no'}
    expect_str = 'Link detected: %s' % stat_map[stat]
    iface_in_vm = utils_net.get_linux_iface_info(mac, session)
    iface_name = iface_in_vm.get('ifname')
    ethtool_cmd = 'ethtool %s' % iface_name
    ethtool_output = session.cmd_output(ethtool_cmd)
    if expect_str in ethtool_output:
        logging.info('link state check PASSED.')
        return True
    else:
        logging.error('link state check FAILED.')
        return False


def check_iface_portgroup(iface_inst, test, params):
    """
    Check iface portgroup attributes

    :param iface_inst: iface instance to be checked
    :param test:  test instance
    :param params: test params
    """
    logging.debug('Attibute dict of Iface to be checked: %s',
                  iface_inst.fetch_attrs())
    net_pgs = eval(params['net_attr_portgroups'])
    logging.debug('Network portgroups configurations dict: %s', net_pgs)
    cur_pg = net_pgs[0] if net_pgs[0]['name'] == 'engineering' \
        else net_pgs[1]
    iface_bw = iface_inst.fetch_attrs()['bandwidth']

    if iface_bw['outbound'] != cur_pg['bandwidth_outbound'] \
            or iface_bw['inbound'] != cur_pg['bandwidth_inbound']:
        test.fail('Bandwidth of iface(%s) is incorrect. '
                  'Should be %s' % (iface_bw, cur_pg))


def update_vm_boot_order(vm_name, disk_boot):
    """
    Update boot order of vm before test

    :param vm_name: vm name
    :param disk_boot: boot order of disk
    """
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    # Remove os boot config to avoid conflict with boot setting of disk device
    vm_os = vmxml.os
    vm_os.del_boots()
    vmxml.os = vm_os
    disk = vmxml.get_devices('disk')[0]
    target_dev = disk.target.get('dev', '')
    logging.debug('Will set boot order %s to device %s',
                  disk_boot, target_dev)
    vmxml.set_boot_order_by_target_dev(target_dev, disk_boot)
    vmxml.sync()


def check_boot_order(vm, test, params):
    """
    Check vm and iface after updating boot order

    :param vm: test vm
    :param test: test instance
    :param params: test params
    """
    if params.get('cold_update') != 'yes' and vm.is_dead():
        test.fail('VM should be alive after live update.')
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm.name)
    iface = vmxml.get_devices('interface')[0]
    status_error = 'yes' == params.get('status_error', 'no')
    if (iface.boot != params.get('new_iface_boot')) ^ status_error:
        test.fail('Boot order update does not meet expectation. '
                  'Update should %s.\n'
                  'Detail: iface.boot = %s new_iface_boot = %s' %
                  ('fail' if status_error else 'succeed',
                   iface.boot, params.get('new_iface_boot')))


def run(test, params, env):
    """
    Test interface devices update
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    network_name = params.get('network_name', 'default')
    new_network_name = params.get("net_name")
    expect_error = "yes" == params.get("status_error", "no")
    expect_err_msg = params.get("expect_err_msg")
    case = params.get('case', '')

    for key, value in params.items():
        if key.startswith('iface_') or key.startswith('new_iface_'):
            expr = '{key} = """{val}"""'.format(key=key, val=value)
            logging.debug('Executing %s', expr)
            exec(expr)
        if key.startswith('del_'):
            expr = '{key} = "yes" == params.get("{key}")'.format(key=key)
            logging.debug('Executing %s', expr)
            exec(expr)

    iface_mtu = params.get("iface_mtu")
    iface_rom = params.get("iface_rom")

    new_iface_inbound = params.get("new_iface_inbound")
    new_iface_link = params.get("new_iface_link")
    new_iface_source = params.get("new_iface_source")
    new_iface_filter = params.get("new_iface_filter")
    new_iface_coalesce = params.get('new_iface_coalesce')
    new_iface_boot = params.get('new_iface_boot')
    new_iface_alias = params.get("new_iface_alias")
    new_iface_filter_parameters = eval(params.get("new_iface_filter_parameters", "{}"))

    del_filter = "yes" == params.get("del_filter")
    del_coalesce = 'yes' == params.get('del_coalesce', 'no')

    create_new_net = "yes" == params.get("create_new_net")
    cold_update = "yes" == params.get("cold_update", "no")
    check_libvirtd = "yes" == params.get("check_libvirtd")
    rules = eval(params.get("rules", "{}"))
    direct_net = 'yes' == params.get('direct_net', 'no')
    direct_mode = params.get("direct_mode", "bridge")

    del_net_bandwidth = 'yes' == params.get('del_net_bandwidth', 'no')

    # Backup the vm xml for recover at last
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    current_net = virsh.net_list("--all").stdout.strip()
    netxml_backup = None
    if network_name in current_net:
        netxml_backup = network_xml.NetworkXML.new_from_net_dumpxml(network_name)

    try:
        # Prepare network
        # Create new network if need
        if create_new_net:
            if direct_net:
                net_ifs = utils_net.get_net_if(state="UP")
                net_forward = str({'dev': net_ifs[0], 'mode': direct_mode})
                net_dict = {'net_forward': net_forward}
            else:
                net_dict = params
            new_net_xml = libvirt.create_net_xml(new_network_name, net_dict)
            new_net_xml.sync()

        else:
            netxml = network_xml.NetworkXML.new_from_net_dumpxml(network_name)
            logging.debug('Network xml before update:\n%s', netxml)
            if del_net_bandwidth:
                netxml.del_element('/bandwidth')
                netxml.sync()
            if case == 'update_portgroup':
                net_dict = {k[9:]: eval(
                    params[k]) for k in params
                    if k.startswith('net_attr_')}
                logging.debug('New net attributes: %s', net_dict)
                netxml.setup_attrs(**net_dict)
                netxml.sync()
            logging.debug('Network xml after prepare:\n%s', netxml)

        # According to the different os find different file for rom
        if (iface_rom and "file" in eval(iface_rom)
                and "%s" in eval(iface_rom)['file']):
            if rpm.RpmBackend().check_installed('ipxe-roms-qemu', '20200823'):
                logging.debug("Update the file path since "
                              "ipxe-20200823-5:")
                iface_rom_new = iface_rom.replace('qemu-kvm', 'ipxe/qemu')
                iface_rom = iface_rom_new
            if os.path.exists(eval(iface_rom)['file'] % "pxe"):
                iface_rom = iface_rom % "pxe"
            elif os.path.exists(eval(iface_rom)['file'] % "efi"):
                iface_rom = iface_rom % "efi"
            else:
                logging.error("Can not find suitable rom file")
        iface_dict_bef = {}
        iface_dict_aft = {}
        names = locals()
        # Collect need update items in 2 dicts for both start vm before and after
        update_list_bef = [
            "driver", 'driver_host', 'driver_guest', "model", "mtu", "rom",
            "filter", 'boot', 'coalesce', 'source'
        ]
        for update_item_bef in update_list_bef:
            if names.get('iface_'+update_item_bef):
                iface_dict_bef.update({update_item_bef: names['iface_'+update_item_bef]})

        update_list_aft = [
            "driver", "driver_host", "driver_guest", "model", "rom", "inbound",
            "outbound", "link", "source", "target", "addr", "filter", "mtu",
            "type", "alias", "filter_parameters", "coalesce", "boot",
        ]
        for update_item_aft in update_list_aft:
            if names.get("new_iface_"+update_item_aft):
                iface_dict_aft.update({update_item_aft: names["new_iface_"+update_item_aft]})

        del_list = ["del_addr", "del_rom", "del_filter", "del_mac",
                    "del_coalesce", "del_bandwidth"]
        for del_item in del_list:
            if names.get(del_item):
                iface_dict_aft.update({del_item: names[del_item]})

        logging.info("iface_dict_bef is %s, iface_dict_aft is %s",
                     iface_dict_bef, iface_dict_aft)

        # Operations before updating vm's iface xml
        disk_boot = params.get('disk_boot')
        if disk_boot:
            update_vm_boot_order(vm_name, disk_boot)

        if case == 'update_driver_iommu_ast':
            iommu_attrs = eval(params.get('iommu_attrs', '{}'))
            libvirt_virtio.add_iommu_dev(vm, iommu_attrs)

        # Update vm interface with items in iface_dict_bef and start it
        if iface_dict_bef:
            libvirt.modify_vm_iface(vm_name, "update_iface", iface_dict_bef)
        logging.info("vm xml is %s", vm.get_xml())

        if not cold_update:
            vm.start()

        if iface_mtu:
            # Do check for mtu size after start vm
            target_dev = libvirt.get_interface_details(vm_name)[0]['interface']
            cmd = "ip link show %s | grep 'mtu %s'" % (target_dev, eval(iface_mtu)['size'])

            def check_mtu():
                """
                Check the mtu setting take effect for interface
                """
                ret = process.run(cmd, ignore_status=True, shell=True)
                if ret.exit_status:
                    test.fail("Can not find mtu setting in cmd result")

            check_mtu()
            utils_libvirtd.libvirtd_restart()
            check_mtu()

        # Do update for iface_driver
        logging.info('Creating new iface xml.')
        new_iface_xml = libvirt.modify_vm_iface(vm_name, "get_xml", iface_dict_aft)
        bef_pid = process.getoutput("pidof -s libvirtd")
        ret = virsh.update_device(vm_name, new_iface_xml, ignore_status=True, debug=True)
        libvirt.check_exit_status(ret, expect_error)
        if check_libvirtd:
            aft_pid = process.getoutput("pidof -s libvirtd")
            if aft_pid != bef_pid:
                test.fail("libvirtd crash after update-device!")
            else:
                logging.info("libvirtd do not crash after update-device!")
        if expect_error:
            real_err_msg = ret.stderr.strip()
            if not re.search(expect_err_msg, real_err_msg, re.IGNORECASE):
                test.fail("The real error msg:'%s' does not match expect one:"
                          '%s' % (real_err_msg, expect_err_msg))
            else:
                logging.info("Get expect result: %s", real_err_msg)
        else:
            if new_iface_inbound:
                iface_bandwidth = {}
                iface_bandwidth = vm_xml.VMXML.get_iftune_params(vm_name)
                for bound_para in ["inbound", "outbound"]:
                    for tune_para in ["average", "peak", "burst"]:
                        get_value = iface_bandwidth.get(bound_para).get(tune_para)
                        expect_value = eval(names["new_iface_"+bound_para]).get(tune_para)
                        logging.info("Get value for %s:%s is %s, expect is %s",
                                     bound_para, tune_para, get_value, expect_value)
                        if get_value != expect_value:
                            test.fail("Get value is not equal to expect")
            vmxml_aft = vm_xml.VMXML.new_from_dumpxml(vm_name)
            iface_aft = list(vmxml_aft.get_iface_all().values())[0]
            iface_inst = Interface()
            iface_inst.xml = element_tree.tostring(iface_aft)
            logging.debug('Interface after update:\n%s', iface_inst)
            if new_iface_link:
                iface_link_value = iface_aft.find('link').get('state')
                if iface_link_value == new_iface_link:
                    logging.info("Find link state is %s in xml", new_iface_link)

                    # Checking the statue in guest
                    mac_addr = iface_aft.find('mac').get('address')
                    state_map = "%s.*\n.*%s" % (iface_link_value.upper(), mac_addr)
                    session = vm.wait_for_serial_login()
                    logging.info("ip link output:%s", session.cmd_output("ip link"))
                    if_name = utils_net.get_net_if(runner=session.cmd_output, state=state_map)[0]
                    if not check_iface_link(session, mac_addr, new_iface_link):
                        test.fail('iface link check inside vm failed.')
                    session.close()
                    if if_name:
                        logging.info("Find iface state %s for %s", iface_link_value, mac_addr)
                    else:
                        test.fail("Can not find iface with mac %s and state %s"
                                  % (mac_addr, iface_link_value))
                else:
                    test.fail("Check fail to get link state, expect %s, but get %s"
                              % (iface_link_value, new_iface_link))
            if create_new_net and new_iface_source:
                iface_source_value = iface_aft.find('source').get('network')
                if iface_source_value == eval(new_iface_source)['network']:
                    logging.info("Get %s in xml as set", iface_source_value)
                else:
                    test.fail("Get source %s is not equal to set %s"
                              % (iface_source_value, new_iface_source))
            if new_iface_filter:
                iface_filter_value = iface_aft.find('filterref').get('filter')
                if iface_filter_value == new_iface_filter:
                    logging.info("Get %s in xml as set", iface_filter_value)
                else:
                    test.fail("Get filter %s is not equal to set %s"
                              % (iface_filter_value, new_iface_filter))
            if new_iface_filter_parameters:
                ebtables_outputs = process.run("ebtables -t nat -L", shell=True).stdout_text
                for rule in rules:
                    if rule not in ebtables_outputs:
                        test.fail("Can not find the corresponding rule after update filter with parameters!")
            if del_filter:
                # if the filter is deleted, it should not exists in the xml and the rules should be deleted as well
                iface_filter_value = iface_aft.find('filterref')
                if iface_filter_value is not None:
                    test.fail("After delete, the filter still exists: %s" % iface_filter_value)
                ebtables_outputs = process.run("ebtables -t nat -L", shell=True).stdout_text
                logging.debug("after nwfilter deleted, ebtables rules are %s" % ebtables_outputs)
                time.sleep(5)
                entries_num = re.findall(r'entries:\s+(\d)', ebtables_outputs)
                for i in entries_num:
                    if i != '0':
                        test.fail("After delete, the rules are still exists!")
            if new_iface_alias:
                iface_alias_value = iface_aft.find('alias').get('name')
                if iface_alias_value == eval(new_iface_alias)['name']:
                    logging.info("Get %s in xml as set", iface_alias_value)
                else:
                    test.fail("Get alias %s is not equal to set %s"
                              % (iface_alias_value, new_iface_alias))
            if 'update_coalesce' in params['name'] or new_iface_coalesce:
                iface_coalesce_val = iface_aft.find('coalesce').find('rx').find('frames').get('max')
                if iface_coalesce_val == str(eval(new_iface_coalesce)['max']):
                    logging.info('coalesce update check PASS.')
                else:
                    test.fail('coalesce value not updated.')
            if del_coalesce:
                if iface_aft.find('coalesce') is None:
                    logging.info('coalesce delete check PASS.')
                else:
                    test.fail('coalesce not deleted.')
            if case == 'update_portgroup':
                check_iface_portgroup(iface_inst, test, params)
            if case == 'update_boot_order':
                check_boot_order(vm, test, params)

    finally:
        vmxml_backup.sync()
        if netxml_backup:
            netxml_backup.sync()
        if create_new_net:
            new_net_xml.undefine()
