import logging
import re
import os
import time

from avocado.utils import process

from virttest import libvirt_vm
from virttest import virsh
from virttest import utils_net
from virttest import utils_libvirtd
from virttest import data_dir
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.interface import Interface


def set_options(iface_type=None, iface_source=None, iface_mac=None,
                suffix="", operation_type="attach", iface_target=None,
                iface_model=None, iface_inbound=None, iface_outbound=None):
    """
    Set attach-detach-interface options.

    :param iface_type: network interface type
    :param iface_source: source of network interface
    :param iface_mac: interface mac address
    :param suffix: attach/detach interface options
    :param operation_type: attach or detach
    :param iface_target: target network name
    :param iface_model: interface mode type
    :param iface_inbound: value of control domain's incoming traffics
    :param iface_outbound: value of control domain's outgoing traffics
    """
    options = ""
    if iface_type is not None:
        options += " --type '%s'" % iface_type
    if iface_source is not None and operation_type == "attach":
        options += " --source '%s'" % iface_source
    if iface_mac is not None:
        options += " --mac '%s'" % iface_mac
    if iface_target is not None:
        options += " --target '%s'" % iface_target
    if iface_model is not None:
        options += " --model '%s'" % iface_model
    if iface_inbound is not None:
        options += " --inbound '%s'" % iface_inbound
    if iface_outbound is not None:
        options += " --outbound '%s'" % iface_outbound
    if suffix:
        options += " %s" % suffix
    return options


def check_dumpxml_iface(vm_name, checked_dict=None):
    """
    Check interfaces in vm's XML file to get matched one.

    :param vm_name: name of domain
    :param checked_dict: all need checked items in this dict
    :return: a tuple with a status and an output
    """
    iface_features = vm_xml.VMXML.get_iface_by_mac(vm_name, checked_dict['mac'])
    if iface_features is not None:
        logging.info("vm current iface dict is %s", iface_features)
        for key in checked_dict.keys():
            if checked_dict[key] is not None:
                if isinstance(iface_features[key], dict):
                    value = eval(checked_dict[key])
                else:
                    value = checked_dict[key]
                if value != iface_features[key]:
                    return (1, ("Interface %s(%s) doesn't match %s."
                                % (key, value, iface_features[key])))
        logging.info("All check pass for interface")
    else:
        return (1, "Can not find interface with mac(%s) in xml." % checked_dict['mac'])
    return (0, "")


def login_to_check(vm, checked_mac):
    """
    Login to vm to get matched interface according its mac address.

    :param vm: name of domain
    :param checked_mac: the mac need to be checked
    """
    try:
        session = vm.wait_for_login()
    except Exception as detail:  # Do not care Exception's type
        return (1, "Can not login to vm:%s" % detail)
    time.sleep(5)
    status, output = session.cmd_status_output("ip -4 -o link list")
    if status != 0:
        return (1, "Login to check failed.")
    else:
        if not re.search(checked_mac, output):
            return (1, ("Can not find interface with mac(%s) in vm:%s"
                        % (checked_mac, output)))
    return (0, "")


def format_param(iface_dict):
    """
    Change the param formate to interface class mapping data

    :param iface_dict: interface properties
    """
    logging.info("iface_dict is %s", iface_dict)
    format_param = iface_dict.copy()
    if iface_dict['source'] is not None:
        format_param['source'] = iface_dict['source']
        if iface_dict['type'] == 'direct':
            format_param['source'] = str({'dev': iface_dict['source'], 'mode': iface_dict['mode']})
    if iface_dict['target'] is not None:
        format_param['target'] = str({'dev': iface_dict['target']})
    if iface_dict['inbound'] is not None:
        format_param['inbound'] = str({'average': iface_dict['inbound'].split(',')[0],
                                       'peak': iface_dict['inbound'].split(',')[1],
                                       'burst': iface_dict['inbound'].split(',')[2]})
    if iface_dict['outbound'] is not None:
        format_param['outbound'] = str({'average': iface_dict['outbound'].split(',')[0],
                                        'peak': iface_dict['outbound'].split(',')[1],
                                        'burst': iface_dict['outbound'].split(',')[2]})
    format_param.pop("mode")
    logging.info("formatted iface_dict is %s", format_param)
    return format_param


def check_save_restore(vm_name):
    """
    Do save/restore operation and check status
    """
    save_file = os.path.join(data_dir.get_tmp_dir(), vm_name + ".save")
    try:
        result = virsh.save(vm_name, save_file, ignore_status=True, debug=True)
        libvirt.check_exit_status(result)
        result = virsh.restore(save_file, ignore_status=True, debug=True)
        libvirt.check_exit_status(result)
    finally:
        os.remove(save_file)


def check_interface_xml(vm_name, iface_type, iface_source, iface_mac, is_active=True):
    """
    Check attached interface xml.

    :param vm_name: vm_name
    :param iface_type: interface device type
    :param iface_source: interface source
    :param iface_mac: interface MAC address
    :param is_active: indicate active or inactive xml
    :return: True/False checked if interface xml match or not
    """
    if is_active:
        dumped_vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    else:
        dumped_vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    ifaces = dumped_vmxml.devices.by_device_tag('interface')
    for iface in ifaces:
        if iface.type_name != iface_type:
            continue
        if iface.mac_address != iface_mac:
            continue
        if iface_source is not None:
            if iface.xmltreefile.find('source') is not None:
                if iface.source['network'] != iface_source:
                    continue
            else:
                continue
        # All three conditions met
        logging.debug("Find %s in given iface XML", iface_mac)
        return True
    logging.debug("Not find %s in given iface XML", iface_mac)
    return False


def add_pcie_controller(vm_name):
    """
    Add pcie-to-pci-bridge controller if not exists in vm

    :param vm_name: name of vm
    """
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    pci_controllers = vmxml.get_controllers('pci')
    for controller in pci_controllers:
        if controller.get('model') == 'pcie-to-pci-bridge':
            break
    else:
        contr_dict = {'controller_type': 'pci',
                      'controller_model': 'pcie-to-pci-bridge'}
        cntl_add = libvirt.create_controller_xml(contr_dict)
        libvirt.add_controller(vm_name, cntl_add)


def check_coalesce(vm_name, mac, coalesce):
    """
    Check coalesce info of given interface

    :param vm_name: name of vm the interface is attached to
    :param mac: mac address of the interface
    :param coalesce: coalesce info to be checked
    :return:
    """
    iface_features = vm_xml.VMXML.get_iface_by_mac(vm_name, mac)
    target = iface_features.get('target', {}).get('dev')

    frames = eval(coalesce).get('max')

    ethtool_cmd = 'ethtool -c %s' % target
    ethtool_output = process.run(
        ethtool_cmd, verbose=True, ignore_status=True).stdout_text

    expected_str = 'rx-frames: %s' % frames
    if expected_str not in ethtool_output:
        return 1, '%s not found in ethtool output' % expected_str

    logging.info('Coalesce check PASS.')
    return 0, ''


def run(test, params, env):
    """
    Test virsh {at|de}tach-interface command.

    1) Prepare test environment and its parameters
    2) Attach the required interface
    3) According test type(only attach or both attach and detach):
       a.Go on to test detach(if attaching is correct)
       b.Return GOOD or raise TestFail(if attaching is wrong)
    4) Check if attached interface is correct:
       a.Try to catch it in vm's XML file
       b.Try to catch it in vm
    5) Detach the attached interface
    6) Check result
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    backup_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    # Test parameters
    uri = libvirt_vm.normalize_connect_uri(params.get("connect_uri",
                                                      "default"))
    vm_ref = params.get("at_detach_iface_vm_ref", "domname")
    options_suffix = params.get("at_detach_iface_options_suffix", "")
    status_error = "yes" == params.get("status_error", "no")
    start_vm = params.get("start_vm")
    # Should attach must be pass for detach test.
    correct_attach = "yes" == params.get("correct_attach", "no")
    readonly = ("yes" == params.get("readonly", "no"))

    # Interface specific attributes.
    iface_type = params.get("at_detach_iface_type", "network")
    iface_source = params.get("at_detach_iface_source", "default")
    iface_mode = params.get("at_detach_iface_mode", "vepa")
    iface_mac = params.get("at_detach_iface_mac", "created")
    iface_target = params.get("at_detach_iface_target")
    iface_model = params.get("at_detach_iface_model")
    iface_inbound = params.get("at_detach_iface_inbound")
    iface_outbound = params.get("at_detach_iface_outbound")
    iface_rom = params.get("at_detach_rom_bar")
    iface_link = params.get("at_detach_link_state")
    iface_boot = params.get("at_detach_boot_order")
    iface_driver = params.get("at_detach_iface_driver")
    iface_driver_host = params.get("at_detach_driver_host")
    iface_driver_guest = params.get("at_detach_driver_guest")
    iface_backend = params.get("at_detach_iface_backend")
    iface_coalesce = params.get('at_detach_iface_coalesce')

    save_restore = params.get("save_restore", "no")
    restart_libvirtd = params.get("restart_libvirtd", "no")
    attach_cmd = params.get("attach_cmd", "attach-interface")
    virsh_dargs = {'ignore_status': True, 'debug': True, 'uri': uri}
    validate_xml_result = "yes" == params.get("check_xml_result", "no")
    paused_after_vm_start = "yes" == params.get("paused_after_vm_start", "no")
    machine_type = params.get("machine_type")

    # Get iface name if iface_type is direct
    if iface_type == "direct":
        iface_source = utils_net.get_net_if(state="UP")[0]
    # Get a bridge name for test if iface_type is bridge.
    # If there is no bridge other than virbr0, try to create one
    # or fail test
    if iface_type == "bridge":
        host_bridge = utils_net.Bridge()
        bridge_list = host_bridge.list_br()
        try:
            bridge_list.remove("virbr0")
        except AttributeError:
            pass  # If no virbr0, just pass is ok
        logging.debug("Useful bridges:%s", bridge_list)
        if len(bridge_list):
            iface_source = bridge_list[0]
        else:
            process.run('ip link add name br0 type bridge', ignore_status=False)
            iface_source = 'br0'
            logging.debug("Added bridge br0")

    # Test both detach and attach, So collect info
    # both of them for result check.
    # When something wrong with interface, set it to 1
    fail_flag = 0
    result_info = []

    # Get a mac address if iface_mac is 'created'.
    if iface_mac == "created" or correct_attach:
        iface_mac = utils_net.generate_mac_address_simple()

    names = locals()
    iface_format = get_formatted_iface_dict(names, params.get("vm_arch_name"))

    # for rtl8139 model, need to add pcie bridge
    if iface_model == "rtl8139" and machine_type == "q35":
        add_pcie_controller(vm_name)
        if start_vm == "yes" and not vm.is_alive():
            vm.start()

    try:
        # Generate xml file if using attach-device command
        if attach_cmd == "attach-device":
            # Change boot order to disk
            libvirt.change_boot_order(vm_name, "disk", "1")
            vm.destroy()
            vm.start()
            # Generate attached xml
            new_iface = Interface(type_name=iface_type)
            if any(x in params['name'] for x in ('multiqueue', 'multi_options', 'with_coalesce')):
                tmp_iface_format = iface_format.copy()
                tmp_iface_format.update(
                    {'source': "{'%s': '%s'}" % (
                        iface_type, iface_format['source'])}
                )
                xml_file_tmp = libvirt.modify_vm_iface(vm_name, "get_xml", tmp_iface_format)
            else:
                xml_file_tmp = libvirt.modify_vm_iface(vm_name, "get_xml", iface_format)
            new_iface.xml = xml_file_tmp
            new_iface.del_address()
            xml_file = new_iface.xml

        # To confirm vm's state and make sure os fully started
        if start_vm == "no":
            if vm.is_alive():
                vm.destroy()
        else:
            vm.wait_for_login().close()

        if paused_after_vm_start:
            vm.pause()

        # Set attach-interface domain
        dom_uuid = vm.get_uuid()
        dom_id = vm.get_id()

        if vm_ref == "domname":
            vm_ref = vm_name
        elif vm_ref == "domid":
            vm_ref = dom_id
        elif vm_ref == "domuuid":
            vm_ref = dom_uuid
        elif vm_ref == "hexdomid" and dom_id is not None:
            vm_ref = hex(int(dom_id))

        # Set attach-interface options and Start attach-interface test
        if correct_attach:
            options = set_options("network", "default", iface_mac, "",
                                  "attach", None, iface_model)
            if readonly:
                virsh_dargs.update({'readonly': True, 'debug': True})
            attach_result = virsh.attach_interface(vm_name, options,
                                                   **virsh_dargs)
        else:
            if attach_cmd == "attach-interface":
                options = set_options(iface_type, iface_source, iface_mac,
                                      options_suffix, "attach", iface_target,
                                      iface_model, iface_inbound, iface_outbound)
                attach_result = virsh.attach_interface(vm_ref, options, **virsh_dargs)
            elif attach_cmd == "attach-device":
                attach_result = virsh.attach_device(vm_name, xml_file,
                                                    ignore_status=True, debug=True)
        attach_status = attach_result.exit_status
        logging.debug(attach_result)

        # If attach interface failed.
        if attach_status:
            if not status_error:
                fail_flag = 1
                result_info.append("Attach Failed: %s" % attach_result.stderr)
            elif status_error:
                # Here we just use it to exit, do not mean test failed
                fail_flag = 1
        # If attach interface succeeded.
        else:
            if status_error and not correct_attach:
                fail_flag = 1
                result_info.append("Attach Success with wrong command.")

        if fail_flag and start_vm == "yes":
            vm.destroy()
            if len(result_info):
                test.fail(result_info)
            else:
                # Exit because it is error_test for attach-interface.
                return

        if "print-xml" in options_suffix:
            iface_obj = Interface(type_name=iface_type)
            iface_obj.xml = attach_result.stdout.strip()
            source_type = iface_type if iface_type == 'bridge' else 'dev'
            if (iface_obj.type_name == iface_type
                    and iface_obj.source.get(source_type) == iface_source
                    and iface_obj.target.get('dev') == iface_target
                    and iface_obj.model == iface_model
                    and iface_obj.bandwidth.inbound == eval(iface_format['inbound'])
                    and iface_obj.bandwidth.outbound == eval(iface_format['outbound'])
                    and iface_obj.mac_address == iface_mac):
                logging.info("Print ml all element check pass")
            else:
                test.fail("Print xml do not show as expected")

        # Check dumpxml file whether the interface is added successfully.
        status, ret = check_dumpxml_iface(vm_name, iface_format)
        if "print-xml" not in options_suffix:
            # Check validate_xml_result flag to determine whether apply check_interface_xml.
            if validate_xml_result:
                # If options_suffix contains config, it need dump inactive xml.
                is_active = True
                if options_suffix.count("config"):
                    is_active = False
                # Check dumping VM xml value.
                if not check_interface_xml(vm_name, iface_type, iface_source, iface_mac, is_active):
                    test.fail("Failed to find matched interface values in VM interface xml")
            if status:
                fail_flag = 1
                result_info.append(ret)
        else:
            if status == 0:
                test.fail("Attach interface effect in xml with print-xml option")
            else:
                return

        # Login to domain to check new interface.
        if not vm.is_alive():
            vm.start()
        elif vm.state() == "paused":
            vm.resume()
        vm.wait_for_login().close()

        status, ret = login_to_check(vm, iface_mac)
        if status:
            fail_flag = 1
            result_info.append(ret)

        def _check_coalesce(fail_flag):
            if iface_coalesce:
                stat, ret = check_coalesce(vm_name, iface_mac, iface_coalesce)
                fail_flag = fail_flag | stat
                result_info.append(ret)
                return fail_flag

        # Check coalesce info if needed
        fail_flag = _check_coalesce(fail_flag)

        # Check on host for direct type
        if iface_type == 'direct':
            cmd_result = process.run("ip -d link show test").stdout_text.strip()
            logging.info("cmd output is %s", cmd_result)
            check_patten = ("%s@%s.*\n.*%s.*\n.*macvtap.*mode.*%s"
                            % (iface_target, iface_source, iface_mac, iface_mode))
            logging.info("check patten is %s", check_patten)
            if not re.search(check_patten, cmd_result):
                logging.error("Can not find %s in ip link" % check_patten)
                fail_flag = 1
                result_info.append(cmd_result)

        # Do operation and check again
        if restart_libvirtd == "yes":
            libvirtd = utils_libvirtd.Libvirtd()
            libvirtd.restart()

        if save_restore == "yes":
            check_save_restore(vm_name)

        # Check coalesce info if needed after save/restore
        fail_flag = _check_coalesce(fail_flag)

        status, ret = check_dumpxml_iface(vm_name, iface_format)
        if status:
            fail_flag = 1
            result_info.append(ret)

        # Set detach-interface options
        options = set_options(iface_type, None, iface_mac,
                              options_suffix, "detach")

        # Start detach-interface test
        if save_restore == "yes" and vm_ref == dom_id:
            vm_ref = vm_name
        detach_result = virsh.detach_interface(
            vm_ref, options, wait_remove_event=True, **virsh_dargs)
        detach_status = detach_result.exit_status
        detach_msg = detach_result.stderr.strip()

        logging.debug(detach_result)

        if detach_status == 0 and status_error == 0:
            # If command with --config parameter, ignore below checking.
            if options_suffix.count("config"):
                return
            # Check the xml after detach and clean up if needed.
            time.sleep(5)
            status, _ = check_dumpxml_iface(vm_name, iface_format)
            if status == 0:
                detach_status = 1
                detach_msg = "xml still exist after detach"
                cleanup_options = "--type %s --mac %s" % (iface_type, iface_mac)
                virsh.detach_interface(vm_ref, cleanup_options, **virsh_dargs)
            else:
                logging.info("After detach, the interface xml disappeared")

        # Check results.
        if status_error:
            if detach_status == 0:
                test.fail("Detach Success with wrong command.")
        else:
            if detach_status != 0:
                test.fail("Detach Failed: %s" % detach_msg)
            else:
                if fail_flag:
                    test.fail("Attach-Detach Success but "
                              "something wrong with its "
                              "functional use:%s" % result_info)
    finally:
        if vm.is_alive():
            vm.destroy()
        backup_xml.sync()


def get_formatted_iface_dict(names, vm_arch_name):
    """
    Create the dictionary of interface configuration parameters
    :param names: Dictionary of variables holding values for the
    configuration parameters, names are expected to be
    { iface_<xml_name>:value }
    :param vm_arch_name: Architecture name, e.g. x86_64
    :return: Dictionary holding configuration for iface xml
    """
    iface_dict = {}

    update_list = [
        "driver", "driver_host", "driver_guest", "model",
        "inbound", "outbound", "link", "target", "mac", "source",
        "boot", "backend", "type", "mode", "coalesce"
    ]
    # For s390-virtio interface addresses are of type ccw
    # rom tuning is only allowed for type pci
    if not vm_arch_name == "s390x":
        update_list.append("rom")

    for update_item in update_list:
        iface_dict.update({update_item: names.get("iface_" + update_item)})
    return format_param(iface_dict)
