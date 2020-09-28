import logging
import time
import ast

from virttest import utils_libvirtd
from virttest import utils_misc
from virttest import utils_net
from virttest import virsh
from virttest import virt_vm
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.interface import Interface
from virttest.utils_test import libvirt
from avocado.utils import process


def run(test, params, env):
    """
    Test interafce xml options.

    1.Prepare test environment,destroy or suspend a VM.
    2.Edit xml and start the domain.
    3.Perform test operation.
    4.Recover test environment.
    5.Confirm the test result.
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    if vm.is_alive():
        vm.wait_for_login()

    def create_iface_xml(mac=None):
        """
        Create interface xml file
        """
        iface = Interface(type_name=iface_type)
        iface.source = iface_source
        iface.model = iface_model if iface_model else "virtio"
        if iface_target:
            iface.target = {'dev': iface_target}
        if mac:
            iface.mac_address = mac
        if iface_rom:
            iface.rom = eval(iface_rom)
        logging.debug("Create new interface xml: %s", iface)
        return iface

    def get_all_mac_in_vm():
        """
        get the mac address list of all the interfaces from a running vm os

        return: a list of the mac addresses
        """
        mac_list = []
        interface_list = vm.get_interfaces()
        for iface_ in interface_list:
            mac_ = vm.get_interface_mac(iface_)
            mac_list.append(mac_)
        return mac_list

    # Interface specific attributes.
    iface_num = params.get("iface_num", '1')
    iface_type = params.get("iface_type", "network")
    iface_source = eval(params.get("iface_source",
                                   "{'network':'default'}"))
    iface_model = params.get("iface_model")
    iface_target = params.get("iface_target")
    iface_mac = params.get("iface_mac")
    iface_rom = params.get("iface_rom")
    attach_device = "yes" == params.get("attach_device", "no")
    attach_iface = "yes" == params.get("attach_iface", "no")
    attach_option = params.get("attach_option", "")
    detach_device = "yes" == params.get("detach_device")
    stress_test = "yes" == params.get("stress_test")
    restart_libvirtd = "yes" == params.get("restart_libvirtd",
                                           "no")
    start_vm = "yes" == params.get("start_vm", "yes")
    options_test = "yes" == params.get("options_test", "no")
    username = params.get("username")
    password = params.get("password")
    poll_timeout = int(params.get("poll_timeout", 10))
    err_msgs1 = params.get("err_msgs1")
    err_msgs2 = params.get("err_msgs2")
    err_msg_rom = params.get("err_msg_rom")
    del_pci = "yes" == params.get("add_pci", "no")
    del_mac = "yes" == params.get("del_mac", "no")
    set_pci = "yes" == params.get("set_pci", "no")
    set_mac = "yes" == params.get("set_mac", "no")
    status_error = "yes" == params.get("status_error", "no")
    pci_addr = params.get("pci")
    check_mac = "yes" == params.get("check_mac", "no")
    vnet_mac = params.get("vnet_mac", None)

    # stree_test require detach operation
    stress_test_detach_device = False
    stress_test_detach_interface = False
    if stress_test:
        if attach_device:
            stress_test_detach_device = True
        if attach_iface:
            stress_test_detach_interface = True

    # The following detach-device step also using attach option
    detach_option = attach_option

    # Back up xml file.
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    #iface_mac = vm_xml.VMXML.get_first_mac_by_name(vm_name)
    libvirtd = utils_libvirtd.Libvirtd()

    # Check virsh command option
    check_cmds = []
    sep_options = attach_option.split()
    logging.debug("sep_options: %s" % sep_options)
    for sep_option in sep_options:
        if attach_device and sep_option:
            check_cmds.append(('attach-device', sep_option))
        if attach_iface and sep_option:
            check_cmds.append(('attach-interface', sep_option))
        if (detach_device or stress_test_detach_device) and sep_option:
            check_cmds.append(('detach-device', sep_option))
        if stress_test_detach_interface and sep_option:
            check_cmds.append(('detach-device', sep_option))

    for cmd, option in check_cmds:
        libvirt.virsh_cmd_has_option(cmd, option)

    try:
        try:
            # Attach an interface when vm is running
            iface_list = []
            err_msgs = ("No more available PCI slots",
                        "No more available PCI addresses")
            if not start_vm:
                virsh.destroy(vm_name)
            for i in range(int(iface_num)):
                if attach_device:
                    logging.info("Try to attach device loop %s" % i)
                    if iface_mac:
                        mac = iface_mac
                        iface_xml_obj = create_iface_xml(mac)
                    elif check_mac:
                        iface_xml_obj = create_iface_xml()
                    else:
                        mac = utils_net.generate_mac_address_simple()
                        iface_xml_obj = create_iface_xml(mac)
                    iface_xml_obj.xmltreefile.write()
                    if check_mac:
                        mac_bef = get_all_mac_in_vm()
                    ret = virsh.attach_device(vm_name, iface_xml_obj.xml,
                                              flagstr=attach_option,
                                              ignore_status=True,
                                              debug=True)
                elif attach_iface:
                    logging.info("Try to attach interface loop %s" % i)
                    if iface_mac:
                        mac = iface_mac
                    else:
                        mac = utils_net.generate_mac_address_simple()
                    options = ("%s %s --model %s --mac %s %s" %
                               (iface_type, iface_source['network'],
                                iface_model, mac, attach_option))
                    ret = virsh.attach_interface(vm_name, options,
                                                 ignore_status=True)
                if ret.exit_status:
                    if any([msg in ret.stderr for msg in err_msgs]):
                        logging.debug("No more pci slots, can't attach more devices")
                        break
                    elif ret.stderr.count("doesn't support option %s" % attach_option):
                        test.cancel(ret.stderr)
                    elif err_msgs1 in ret.stderr:
                        logging.debug("option %s is not supported when domain running is %s" % (attach_option, vm.is_alive()))
                        if start_vm or ("--live" not in sep_options and attach_option):
                            test.fail("return not supported, but it is unexpected")
                    elif err_msgs2 in ret.stderr:
                        logging.debug("options %s are mutually exclusive" % attach_option)
                        if not ("--current" in sep_options and len(sep_options) > 1):
                            test.fail("return mutualy exclusive, but it is unexpected")
                    elif err_msg_rom and err_msg_rom in ret.stderr:
                        logging.debug("Attach failed with expect err msg: %s" % err_msg_rom)
                    else:
                        test.fail("Failed to attach-interface: %s" % ret.stderr.strip())
                elif stress_test:
                    if attach_device:
                        # Detach the device immediately for stress test
                        ret = virsh.detach_device(vm_name, iface_xml_obj.xml,
                                                  flagstr=detach_option,
                                                  ignore_status=True)
                    elif attach_iface:
                        # Detach the device immediately for stress test
                        options = ("--type %s --mac %s %s" %
                                   (iface_type, mac, detach_option))
                        ret = virsh.detach_interface(vm_name, options,
                                                     ignore_status=True)
                    libvirt.check_exit_status(ret)
                else:
                    if attach_device:
                        if check_mac:
                            mac_aft = get_all_mac_in_vm()
                            add_mac = list(set(mac_aft).difference(set(mac_bef)))
                            try:
                                mac = add_mac[0]
                                logging.debug("The mac address of the attached interface is %s" % mac)
                            except IndexError:
                                test.fail("Can not find the new added interface in the guest os!")
                        iface_list.append({'mac': mac,
                                           'iface_xml': iface_xml_obj})
                    elif attach_iface:
                        iface_list.append({'mac': mac})
            # Restart libvirtd service
            if restart_libvirtd:
                libvirtd.restart()
                # After restart libvirtd, the old console was invalidated,
                # so we need create a new serial console
                vm.cleanup_serial_console()
                vm.create_serial_console()
            # in options test, check if the interface is attached
            # in current state when attach return true

            def check_iface_exist():
                try:
                    session = vm.wait_for_serial_login(username=username,
                                                       password=password)
                    if utils_net.get_linux_ifname(session, iface['mac']):
                        return True
                    else:
                        logging.debug("can not find interface in vm")
                except Exception:
                    return False
            if options_test:
                for iface in iface_list:
                    if 'mac' in iface:
                        # Check interface in dumpxml output
                        if_attr = vm_xml.VMXML.get_iface_by_mac(vm_name,
                                                                iface['mac'])
                        if vm.is_alive() and attach_option == "--config":
                            if if_attr:
                                test.fail("interface should not exists "
                                          "in current live vm while "
                                          "attached by --config")
                        else:
                            if if_attr:
                                logging.debug("interface %s found current "
                                              "state in xml" % if_attr['mac'])
                            else:
                                test.fail("no interface found in "
                                          "current state in xml")

                        if if_attr:
                            if if_attr['type'] != iface_type or \
                                    if_attr['source'] != \
                                    iface_source['network']:
                                test.fail("Interface attribute doesn't "
                                          "match attachment options")
                        # check interface in vm only when vm is active
                        if vm.is_alive():
                            logging.debug("check interface in current state "
                                          "in vm")

                            if not utils_misc.wait_for(check_iface_exist, timeout=20):
                                if not attach_option == "--config":
                                    test.fail("Can't see interface "
                                              "in current state in vm")
                                else:
                                    logging.debug("find interface in "
                                                  "current state in vm")
                            else:
                                logging.debug("find interface in "
                                              "current state in vm")
                            # in options test, if the attach is performed
                            # when the vm is running
                            # need to destroy and start to check again
                            vm.destroy()

            # Start the domain if needed
            if vm.is_dead():
                vm.start()
            session = vm.wait_for_serial_login(username=username,
                                               password=password)

            # check if interface is attached
            for iface in iface_list:
                if 'mac' in iface:
                    logging.debug("check interface in xml")
                    # Check interface in dumpxml output
                    if_attr = vm_xml.VMXML.get_iface_by_mac(vm_name,
                                                            iface['mac'])
                    logging.debug(if_attr)
                    if if_attr:
                        logging.debug("interface {} is found in xml".
                                      format(if_attr['mac']))
                        if (if_attr['type'] != iface_type or
                                if_attr['source'] != iface_source['network']):
                            test.fail("Interface attribute doesn't "
                                      "match attachment options")
                        if options_test and start_vm and attach_option \
                                in ("--current", "--live", ""):
                            test.fail("interface should not exists when "
                                      "restart vm in options_test")
                    else:
                        logging.debug("no interface found in xml")
                        if options_test and start_vm and attach_option in \
                                ("--current", "--live", ""):
                            logging.debug("interface not exists next state "
                                          "in xml with %s" % attach_option)
                        else:
                            test.fail("Can't see interface in dumpxml")

                    # Check interface on guest
                    if not utils_misc.wait_for(check_iface_exist, timeout=20):
                        logging.debug("can't see interface next state in vm")
                        if start_vm and attach_option in \
                                ("--current", "--live", ""):
                            logging.debug("it is expected")
                        else:
                            test.fail("should find interface "
                                      "but no seen in next state in vm")
                    if vnet_mac:
                        # get the name of the backend tap device
                        iface_params = vm_xml.VMXML.get_iface_by_mac(vm_name, mac)
                        target_name = iface_params['target']['dev']
                        # check the tap device mac on host
                        tap_info = process.run("ip l show %s" % target_name, shell=True, ignore_status=True).stdout_text
                        logging.debug("vnet_mac should be %s" % vnet_mac)
                        logging.debug("check on host for the details of tap device %s: %s" % (target_name, tap_info))
                        if vnet_mac not in tap_info:
                            test.fail("The mac address of the tap device do not match!")
            # Detach hot/cold-plugged interface at last
            if detach_device:
                logging.debug("detach interface here:")
                if attach_device:
                    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
                    iface_xml_ls = vmxml.get_devices("interface")
                    iface_xml_ls_det = iface_xml_ls[1:]
                    for iface_xml_det in iface_xml_ls_det:
                        if del_mac:
                            iface_xml_det.del_mac_address()
                        if del_pci:
                            iface_xml_det.del_address()
                        if set_mac:
                            mac = utils_net.generate_mac_address_simple()
                            iface_xml_det.set_mac_address(mac)
                        if set_pci:
                            pci_dict = ast.literal_eval(pci_addr)
                            addr = iface_xml_det.new_iface_address(**{"attrs": pci_dict})
                            iface_xml_det.set_address(addr)
                        ori_pid_libvirtd = process.getoutput("pidof libvirtd")
                        ret = virsh.detach_device(vm_name,
                                                  iface_xml_det.xml,
                                                  flagstr="",
                                                  ignore_status=True)
                        libvirt.check_exit_status(ret, status_error)
                        aft_pid_libvirtd = process.getoutput("pidof libvirtd")
                        if not utils_libvirtd.Libvirtd.is_running or ori_pid_libvirtd != aft_pid_libvirtd:
                            test.fail("Libvirtd crash after detach non-exists interface")
                else:
                    for iface in iface_list:
                        options = ("%s --mac %s" %
                                   (iface_type, iface['mac']))
                        ret = virsh.detach_interface(vm_name, options,
                                                     ignore_status=True)
                        libvirt.check_exit_status(ret)

                # Check if interface was detached
                if not status_error:
                    for iface in iface_list:
                        if 'mac' in iface:
                            polltime = time.time() + poll_timeout
                            while True:
                                # Check interface in dumpxml output
                                if not vm_xml.VMXML.get_iface_by_mac(vm_name,
                                                                     iface['mac']):
                                    break
                                else:
                                    time.sleep(2)
                                    if time.time() > polltime:
                                        test.fail("Interface still "
                                                  "exists after detachment")
            session.close()
        except virt_vm.VMStartError as e:
            logging.info(str(e))
            test.fail('VM failed to start:\n%s' % e)

    finally:
        # Recover VM.
        logging.info("Restoring vm...")
        if vm.is_alive():
            vm.destroy(gracefully=False)
        vmxml_backup.sync()
