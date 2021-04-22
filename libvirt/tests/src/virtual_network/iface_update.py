import os
import re
import logging
import time

from avocado.utils import process

from virttest import virsh
from virttest import utils_net
from virttest import utils_libvirtd
from virttest.libvirt_xml import network_xml
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


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

    iface_driver = params.get("iface_driver")
    iface_driver_host = params.get("iface_driver_host")
    iface_driver_guest = params.get("iface_driver_guest")
    iface_model = params.get("iface_model")
    iface_mtu = params.get("iface_mtu")
    iface_rom = params.get("iface_rom")
    iface_filter = params.get("iface_filter")
    iface_boot = params.get('iface_boot')
    iface_coalesce = params.get('iface_coalesce')

    new_iface_driver = params.get("new_iface_driver")
    new_iface_driver_host = params.get("new_iface_driver_host")
    new_iface_driver_guest = params.get("new_iface_driver_guest")
    new_iface_model = params.get("new_iface_model")
    new_iface_rom = params.get("new_iface_rom")
    new_iface_inbound = params.get("new_iface_inbound")
    new_iface_outbound = params.get("new_iface_outbound")
    new_iface_link = params.get("new_iface_link")
    new_iface_source = params.get("new_iface_source")
    new_iface_target = params.get("new_iface_target")
    new_iface_addr = params.get("new_iface_addr")
    new_iface_filter = params.get("new_iface_filter")
    new_iface_mtu = params.get("new_iface_mtu")
    new_iface_type = params.get("new_iface_type")
    create_new_net = "yes" == params.get("create_new_net")
    new_iface_alias = params.get("new_iface_alias")
    new_iface_coalesce = params.get('new_iface_coalesce')
    cold_update = "yes" == params.get("cold_update", "no")
    del_addr = "yes" == params.get("del_address")
    del_rom = "yes" == params.get("del_rom")
    del_filter = "yes" == params.get("del_filter")
    check_libvirtd = "yes" == params.get("check_libvirtd")
    new_iface_filter_parameters = eval(params.get("new_iface_filter_parameters", "{}"))
    rules = eval(params.get("rules", "{}"))
    del_mac = "yes" == params.get("del_mac", "no")
    del_coalesce = 'yes' == params.get('del_coalesce', 'no')

    del_net_bandwidth = 'yes' == params.get('del_net_bandwidth', 'no')

    # Backup the vm xml for recover at last
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    netxml_backup = network_xml.NetworkXML.new_from_net_dumpxml(network_name)

    try:
        # Prepare network
        netxml = network_xml.NetworkXML.new_from_net_dumpxml(network_name)
        logging.debug('Network xml before update:\n%s', netxml)
        if del_net_bandwidth:
            netxml.del_element('/bandwidth')
        logging.debug('Network xml after update:\n%s', netxml)

        # According to the different os find different file for rom
        if (iface_rom and "file" in eval(iface_rom)
                and "%s" in eval(iface_rom)['file']):
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
            "filter", 'boot', 'coalesce'
            ]
        for update_item_bef in update_list_bef:
            if names['iface_'+update_item_bef]:
                iface_dict_bef.update({update_item_bef: names['iface_'+update_item_bef]})

        update_list_aft = [
            "driver", "driver_host", "driver_guest", "model", "rom", "inbound",
            "outbound", "link", "source", "target", "addr", "filter", "mtu", "type",
            "alias", "filter_parameters", "coalesce"
        ]
        for update_item_aft in update_list_aft:
            if names["new_iface_"+update_item_aft]:
                iface_dict_aft.update({update_item_aft: names["new_iface_"+update_item_aft]})
        logging.info("iface_dict_bef is %s, iface_dict_aft is %s",
                     iface_dict_bef, iface_dict_aft)

        del_list = ["del_addr", "del_rom", "del_filter", "del_mac", "del_coalesce"]
        for del_item in del_list:
            if names[del_item]:
                iface_dict_aft.update({del_item: names[del_item]})

        # Operations before updating vm's iface xml
        if iface_boot:
            disk_boot = params.get('disk_book', 1)
            vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
            # Remove os boot config
            vm_os = vmxml.os
            vm_os.del_boots()
            vmxml.os = vm_os
            # Add boot config to disk
            disk = vmxml.get_devices('disk')[0]
            target_dev = disk.target.get('dev', '')
            logging.debug('Will set boot order %s to device %s',
                          disk_boot, target_dev)
            vmxml.set_boot_order_by_target_dev(target_dev, disk_boot)
            vmxml.sync()

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

        # Create new network if need
        if create_new_net:
            new_net_xml = libvirt.create_net_xml(new_network_name, params)
            new_net_xml.sync()

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

    finally:
        vmxml_backup.sync()
        netxml_backup.sync()
        if create_new_net:
            new_net_xml.undefine()
