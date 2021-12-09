import os
import logging

from avocado.utils import process
from avocado.utils import download
from avocado.core import exceptions

from virttest import virsh
from virttest import virt_vm
from virttest import data_dir
from virttest import utils_net
from virttest import utils_test
from virttest import utils_misc
from virttest import libvirt_version

from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.interface import Interface


def run(test, params, env):
    """
    Test virtio/virtio-transitional/virtio-non-transitional model of interface

    :param test: Test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment
    """

    def reboot():
        """
        Shutdown and restart guest, then wait for login
        """
        vm.destroy()
        vm.start()
        return vm.wait_for_login()

    def check_plug_to_pci_bridge(vm_name, mac):
        """
        Check if the nic is plugged onto pcie-to-pci-bridge

        :param vm_name： Vm name
        :param mac:  The mac address of plugged interface
        :return True if plugged onto pcie-to-pci-bridge, otherwise False
        """
        v_xml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        interface = v_xml.get_iface_all()[mac]
        bus = int(eval(interface.find('address').get('bus')))
        controllers = vmxml.get_controllers('pci')
        for controller in controllers:
            if controller.get('index') == bus:
                if controller.get('model') == 'pcie-to-pci-bridge':
                    return True
                break
        return False

    def detect_new_nic(mac):
        """
        Detect the new interface by domiflist

        :param mac: The mac address of plugged interface
        :return plugged interface name
        """
        def check_mac_exist():
            all_infos = libvirt.get_interface_details(vm_name)
            for nic_info in all_infos:
                if nic_info.get('mac') == mac:
                    return nic_info.get('interface')
            return False

        plugged_nic = utils_misc.wait_for(lambda: check_mac_exist(), 5)
        if not plugged_nic:
            test.fail("Failed to plug device %s" % mac)
        return plugged_nic

    def renew_ip_address(session, mac, guest_os_type):
        """
        Renew ip for plugged nic

        :param session: Vm session
        :param mac: The mac address of plugged interface
        :param guest_os_type: Guest os type, Linux or Windows
        """
        if guest_os_type == 'Windows':
            utils_net.restart_windows_guest_network_by_key(session,
                                                           "macaddress",
                                                           mac)
        ifname = utils_net.get_linux_ifname(session, mac)
        utils_net.create_network_script(ifname, mac, 'dhcp', '255.255.255.0')
        utils_net.restart_guest_network(session, mac)
        arp_clean = "arp -n|awk '/^[1-9]/{print \"arp -d \" $1}'|sh"
        session.cmd_output_safe(arp_clean)

    def get_hotplug_nic_ip(vm, nic, session, guest_os_type):
        """
        Get if of the plugged interface

        :param vm: Vm object
        :param nic: Nic object
        :param session: Vm session
        :param guest_os_type: Guest os type, Linux or Windows
        :return: Nic ip
        """
        def __get_address():
            """
            Get ip address and return it, configure a new ip if device
            exists but no ip

            :return: Ip address if get, otherwise None
            """
            try:
                index = [
                    _idx for _idx, _nic in enumerate(
                        vm.virtnet) if _nic == nic][0]
                return vm.wait_for_get_address(index, timeout=90)
            except IndexError:
                test.error(
                    "Nic '%s' not exists in VM '%s'" %
                    (nic["nic_name"], vm.name))
            except (virt_vm.VMIPAddressMissingError,
                    virt_vm.VMAddressVerificationError):
                renew_ip_address(session, nic["mac"], guest_os_type)
            return

        # Wait for ip address is configured for the nic device
        nic_ip = utils_misc.wait_for(__get_address, timeout=360)
        if nic_ip:
            return nic_ip
        cached_ip = vm.address_cache.get(nic["mac"])
        arps = process.system_output("arp -aen").decode()
        logging.debug("Can't get IP address:")
        logging.debug("\tCached IP: %s", cached_ip)
        logging.debug("\tARP table: %s", arps)
        return None

    def check_nic_removed(mac, session):
        """
        Get interface IP address by given MAC addrss. If try_dhclint is
        True, then try to allocate IP addrss for the interface.

        :param mac: The mac address of plugged interface
        :param session: Vm session
        """
        except_mesg = ''
        try:
            if guest_os_type == 'Windows':
                except_mesg = "Get nic netconnectionid failed"
                utils_net.restart_windows_guest_network_by_key(session,
                                                               "macaddress",
                                                               mac)
            else:
                except_mesg = ("Failed to determine interface"
                               " name with mac %s" % mac)
                utils_net.get_linux_ifname(session, mac)
        except exceptions.TestError as e:
            if except_mesg in str(e):
                return True
        else:
            return False

    def attach_nic():  # pylint: disable=W0611
        """
        Attach interface, by xml or cmd, for both hot and cold plug
        """

        def create_iface_xml(mac):
            """
            Create interface xml file

            :param mac: The mac address of nic device
            """
            iface = Interface(type_name='network')
            iface.source = iface_source
            iface.model = iface_model
            iface.mac_address = mac
            logging.debug("Create new interface xml: %s", iface)
            return iface

        plug_method = params.get('plug_method', 'interface')
        cold_plug = params.get('cold_plug', 'no')
        mac = utils_net.generate_mac_address_simple()
        iface_source = {'network': 'default'}
        iface_model = params["virtio_model"]
        options = ("network %s --model %s --mac %s" % (
            iface_source['network'], iface_model, mac))
        nic_params = {'mac': mac, 'nettype': params['nettype'],
                      'ip_version': 'ipv4'}
        if cold_plug == "yes":
            options += ' --config'
        if plug_method == 'interface':  # Hotplug nic vir attach-interface
            ret = virsh.attach_interface(vm_name, options, ignore_status=True)
        else:  # Hotplug nic via attach-device
            nic_xml = create_iface_xml(mac)
            nic_xml.xmltreefile.write()
            xml_file = nic_xml.xml
            with open(xml_file) as nic_file:
                logging.debug("Attach device by XML: %s", nic_file.read())
            ret = virsh.attach_device(
                domainarg=vm_name, filearg=xml_file, debug=True)
        libvirt.check_exit_status(ret)
        if cold_plug == "yes":
            reboot()  # Reboot guest if it is cold plug test
        detect_new_nic(mac)
        if plug_method == 'interface' and cold_plug == 'no':
            check_plug_to_pci_bridge(vm_name, mac)
        session = vm.wait_for_login(serial=True)
        # Add nic to VM object for further check
        nic_name = vm.add_nic(**nic_params)["nic_name"]
        nic = vm.virtnet[nic_name]
        # Config ip inside guest for new added nic
        if not utils_misc.wait_for(
                lambda: get_hotplug_nic_ip(vm, nic, session, guest_os_type),
                timeout=30):
            test.fail("Does not find plugged nic %s in guest" % mac)
        options = ("network %s" % mac)
        if cold_plug == "yes":
            options += ' --config'
        # Detach nic device
        if plug_method == 'interface':
            ret = virsh.detach_interface(vm_name, options, ignore_status=True)
        else:
            with open(xml_file) as nic_file:
                logging.debug("Detach device by XML: %s", nic_file.read())
            ret = virsh.detach_device(
                domainarg=vm_name, filearg=xml_file, debug=True)
        libvirt.check_exit_status(ret)
        if cold_plug == "yes":
            session = reboot()  # Reboot guest if it is cold plug test
        # Check if nic is removed from guest
        if not utils_misc.wait_for(
                lambda: check_nic_removed(mac, session), timeout=30):
            test.fail("The nic %s still exist in guest after being unplugged" %
                      nic_name)

    def save_restore():  # pylint: disable=W0611
        """
        Sub test for save and restore
        """
        save_path = os.path.join(
            data_dir.get_tmp_dir(), '%s.save' % params['os_variant'])
        ret = virsh.save(vm_name, save_path)
        libvirt.check_exit_status(ret)
        ret = virsh.restore(save_path)
        libvirt.check_exit_status(ret)

    def ping_test(restart_network=False):
        """
        Basic ping test for interface

        :param restart_network: True or False. Whether to restart network
        :raise: test.fail if ping test fails
        """
        session = vm.wait_for_login()
        if restart_network:
            utils_net.restart_guest_network(session)

        dest = params.get('ping_dest', 'www.baidu.com')
        status, output = utils_test.ping(dest, 10, session=session,
                                         timeout=20)
        session.close()
        if status != 0:
            test.fail("Ping failed, status: %s,"
                      " output: %s" % (status, output))

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(params["main_vm"])
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()
    guest_src_url = params.get("guest_src_url")
    params['disk_model'] = params['virtio_model']
    guest_os_type = params['os_type']

    target_path = None

    if not libvirt_version.version_compare(5, 0, 0):
        test.cancel("This libvirt version doesn't support "
                    "virtio-transitional model.")

    # Download and replace image when guest_src_url provided
    if guest_src_url:
        image_name = params['image_path']
        target_path = utils_misc.get_path(data_dir.get_data_dir(), image_name)
        if not os.path.exists(target_path):
            download.get_file(guest_src_url, target_path)
        params["blk_source_name"] = target_path
    libvirt.set_vm_disk(vm, params)

    # Add pcie-to-pci-bridge when there is no one
    pci_controllers = vmxml.get_controllers('pci')
    for controller in pci_controllers:
        if controller.get('model') == 'pcie-to-pci-bridge':
            break
    else:
        contr_dict = {'controller_type': 'pci',
                      'controller_model': 'pcie-to-pci-bridge'}
        cntl_add = libvirt.create_controller_xml(contr_dict)
        libvirt.add_controller(vm_name, cntl_add)
    try:  # Update interface model as defined
        iface_params = {'model': params['virtio_model']}
        libvirt.modify_vm_iface(vm_name, "update_iface", iface_params)
        if not vm.is_alive():
            vm.start()
        # Test if nic works well via ping
        ping_test()
        test_step = params.get("sub_test_step")
        if test_step:
            eval(test_step)()
            # Test if nic still work well afeter sub steps test
            ping_test(True)
    finally:
        vm.destroy()
        backup_xml.sync()

        if guest_src_url and target_path:
            libvirt.delete_local_disk("file", path=target_path)
