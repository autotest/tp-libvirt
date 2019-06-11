import os
import re
import logging

from avocado.utils import process

from virttest import virsh
from virttest import utils_net
from virttest import utils_libvirtd
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Test interface devices update
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    new_network_name = params.get("net_name")
    expect_error = "yes" == params.get("status_error", "no")
    expect_err_msg = params.get("expect_err_msg")

    iface_driver = params.get("iface_driver")
    iface_model = params.get("iface_model")
    iface_mtu = params.get("iface_mtu")
    iface_rom = params.get("iface_rom")

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
    cold_update = "yes" == params.get("cold_update", "no")
    del_addr = "yes" == params.get("del_address")
    del_rom = "yes" == params.get("del_rom")

    # Backup the vm xml for recover at last
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    try:
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
            "driver", "model", "mtu", "rom"
            ]
        for update_item_bef in update_list_bef:
            if names['iface_'+update_item_bef]:
                iface_dict_bef.update({update_item_bef: names['iface_'+update_item_bef]})

        update_list_aft = [
            "driver", "driver_host", "driver_guest", "model", "rom", "inbound",
            "outbound", "link", "source", "target", "addr", "filter", "mtu", "type",
            "alias"]
        for update_item_aft in update_list_aft:
            if names["new_iface_"+update_item_aft]:
                iface_dict_aft.update({update_item_aft: names["new_iface_"+update_item_aft]})
        logging.info("iface_dict_bef is %s, iface_dict_aft is %s",
                     iface_dict_bef, iface_dict_aft)

        del_list = ["del_addr", "del_rom"]
        for del_item in del_list:
            if names[del_item]:
                iface_dict_aft.update({del_item: "True"})

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
        new_iface_xml = libvirt.modify_vm_iface(vm_name, "get_xml", iface_dict_aft)
        ret = virsh.update_device(vm_name, new_iface_xml, ignore_status=True, debug=True)
        libvirt.check_exit_status(ret, expect_error)
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
            if new_iface_alias:
                iface_alias_value = iface_aft.find('alias').get('name')
                if iface_alias_value == eval(new_iface_alias)['name']:
                    logging.info("Get %s in xml as set", iface_alias_value)
                else:
                    test.fail("Get alias %s is not equal to set %s"
                              % (iface_alias_value, new_iface_alias))
    finally:
        vmxml_backup.sync()
        if create_new_net:
            new_net_xml.undefine()
