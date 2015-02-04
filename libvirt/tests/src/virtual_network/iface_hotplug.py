import time
import logging
from autotest.client.shared import error
from virttest import virt_vm
from virttest import virsh
from virttest import utils_net
from virttest import utils_libvirtd
from virttest.utils_test import libvirt
from virttest.libvirt_xml.devices.interface import Interface
from virttest.libvirt_xml import vm_xml


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

    def create_iface_xml(mac):
        """
        Create interface xml file
        """
        iface = Interface(type_name=iface_type)
        iface.source = iface_source
        iface.model = iface_model if iface_model else "virtio"
        if iface_target:
            iface.target = {'dev': iface_target}
        iface.mac_address = mac
        logging.debug("Create new interface xml: %s", iface)
        return iface

    status_error = "yes" == params.get("status_error", "no")

    # Interface specific attributes.
    iface_num = params.get("iface_num", '1')
    iface_type = params.get("iface_type", "network")
    iface_source = eval(params.get("iface_source",
                                   "{'network':'default'}"))
    iface_model = params.get("iface_model")
    iface_target = params.get("iface_target")
    iface_mac = params.get("iface_mac")
    attach_device = "yes" == params.get("attach_device", "no")
    attach_iface = "yes" == params.get("attach_iface", "no")
    attach_option = params.get("attach_option", "")
    detach_device = "yes" == params.get("detach_device")
    stress_test = "yes" == params.get("stress_test")
    restart_libvirtd = "yes" == params.get("restart_libvirtd",
                                           "no")

    # Back up xml file.
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    #iface_mac = vm_xml.VMXML.get_first_mac_by_name(vm_name)
    libvirtd = utils_libvirtd.Libvirtd()

    try:
        try:
            # Attach an interface when vm is running
            iface_list = []
            if attach_device:
                for i in range(int(iface_num)):
                    logging.info("Try to attach interface loop %s" % i)
                    if iface_mac:
                        mac = iface_mac
                    else:
                        mac = utils_net.generate_mac_address_simple()
                    iface_xml_obj = create_iface_xml(mac)
                    iface_xml_obj.xmltreefile.write()
                    ret = virsh.attach_device(vm_name, iface_xml_obj.xml,
                                              flagstr=attach_option,
                                              ignore_status=True)
                    if ret.exit_status:
                        if ret.stderr.count("No more available PCI slots"):
                            break
                        elif status_error:
                            continue
                        else:
                            logging.error("Command output %s" %
                                          ret.stdout.strip())
                            raise error.TestFail("Failed to attach-device")
                    elif stress_test:
                        # Detach the device immediately for stress test
                        ret = virsh.detach_device(vm_name, iface_xml_obj.xml,
                                                  flagstr=attach_option,
                                                  ignore_status=True)
                        libvirt.check_exit_status(ret)
                    else:
                        iface_list.append({'mac': mac,
                                           'iface_xml': iface_xml_obj})
                # Need sleep here for attachment take effect
                time.sleep(5)
            elif attach_iface:
                for i in range(int(iface_num)):
                    logging.info("Try to attach interface loop %s" % i)
                    mac = utils_net.generate_mac_address_simple()
                    options = ("%s %s --model %s --mac %s %s" %
                               (iface_type, iface_source['network'],
                                iface_model, mac, attach_option))
                    ret = virsh.attach_interface(vm_name, options,
                                                 ignore_status=True)
                    if ret.exit_status:
                        if ret.stderr.count("No more available PCI slots"):
                            break
                        elif status_error:
                            continue
                        else:
                            logging.error("Command output %s" %
                                          ret.stdout.strip())
                            raise error.TestFail("Failed to attach-interface")
                    elif stress_test:
                        # Detach the device immediately for stress test
                        ret = virsh.attach_interface(vm_name, options,
                                                     ignore_status=True)
                        libvirt.check_exit_status(ret)
                    else:
                        iface_list.append({'mac': mac})
                # Need sleep here for attachment take effect
                time.sleep(5)

            # Restart libvirtd service
            if restart_libvirtd:
                libvirtd.restart()

            # Start the domain if needed
            if vm.is_dead():
                vm.start()
            session = vm.wait_for_login()

            # Check if interface was attached
            for iface in iface_list:
                if iface.has_key('mac'):
                    # Check interface in dumpxml output
                    if_attr = vm_xml.VMXML.get_iface_by_mac(vm_name,
                                                            iface['mac'])
                    if not if_attr:
                        raise error.TestFail("Can't see interface "
                                             " in dumpxml")
                    if (if_attr['type'] != iface_type or
                            if_attr['source'] != iface_source['network']):
                        raise error.TestFail("Interface attribute doesn't"
                                             " match attachment opitons")
                    # Check interface on guest
                    if not utils_net.get_linux_ifname(session,
                                                      iface['mac']):
                        raise error.TestFail("Can't see interface"
                                             " on guest")

            # Detach hot/cold-plugged interface at last
            if detach_device:
                for iface in iface_list:
                    if iface.has_key('iface_xml'):
                        ret = virsh.detach_device(vm_name,
                                                  iface['iface_xml'].xml,
                                                  flagstr="",
                                                  ignore_status=True)
                        libvirt.check_exit_status(ret)
                    else:
                        options = ("%s --mac %s" %
                                   (iface_type, iface['mac']))
                        ret = virsh.detach_interface(vm_name, options,
                                                     ignore_status=True)
                        libvirt.check_exit_status(ret)

                # Check if interface was detached
                for iface in iface_list:
                    if iface.has_key('mac'):
                        # Check interface in dumpxml output
                        if vm_xml.VMXML.get_iface_by_mac(vm_name,
                                                         iface['mac']):
                            raise error.TestFail("Interface still exist"
                                                 " after detachment")
            session.close()
        except virt_vm.VMStartError, e:
            logging.info(str(e))
            raise error.TestFail('VM Failed to start for some reason!')

    finally:
        # Recover VM.
        logging.info("Restoring vm...")
        if vm.is_alive():
            vm.destroy(gracefully=False)
        vmxml_backup.sync()
