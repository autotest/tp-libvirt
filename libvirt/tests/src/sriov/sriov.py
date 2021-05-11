import logging
import os
import re
import glob
import string
import random

from avocado.utils import process

from virttest import virsh
from virttest import utils_net
from virttest import utils_misc
from virttest import utils_package
from virttest import utils_test
from virttest import utils_libvirtd
from virttest.libvirt_xml.nodedev_xml import NodedevXML
from virttest.libvirt_xml import network_xml
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.interface import Interface
from virttest.libvirt_xml.devices.controller import Controller


def run(test, params, env):
    """
    Sriov basic test:

    1.create max vfs;
    2.Check the nodedev info;
    3.Start a guest with vf;
    4.Reboot a guest with vf;
    5.suspend/resume a guest with vf
    """
    def find_pf():
        pci_address = ""
        for pci in pci_dirs:
            temp_iface_name = os.listdir("%s/net" % pci)[0]
            operstate = utils_net.get_net_if_operstate(temp_iface_name)
            if operstate == "up":
                pf_iface_name = temp_iface_name
                pci_address = pci
                break
        if pci_address == "":
            return False
        else:
            return pci_address

    def create_address_dict(pci_id):
        """
            Use pci_xxxx_xx_xx_x to create address dict.
        """
        device_domain = pci_id.split(':')[0]
        device_domain = "0x%s" % device_domain
        device_bus = pci_id.split(':')[1]
        device_bus = "0x%s" % device_bus
        device_slot = pci_id.split(':')[-1].split('.')[0]
        device_slot = "0x%s" % device_slot
        device_function = pci_id.split('.')[-1]
        device_function = "0x%s" % device_function
        attrs = {'type': 'pci', 'domain': device_domain, 'slot': device_slot,
                 'bus': device_bus, 'function': device_function}
        return attrs

    def addr_to_pci(addr):
        """
            Convert address dict to pci address: xxxxx:xx.x.
        """
        pci_domain = re.findall(r"0x(.+)", addr['domain'])[0]
        pci_bus = re.findall(r"0x(.+)", addr['bus'])[0]
        pci_slot = re.findall(r"0x(.+)", addr['slot'])[0]
        pci_function = re.findall(r"0x(.+)", addr['function'])[0]
        pci_addr = pci_domain + ":" + pci_bus + ":" + pci_slot + "." + pci_function
        return pci_addr

    def create_hostdev_interface(pci_id, managed, model):
        """
            Create hostdev type interface xml.
        """
        attrs = create_address_dict(pci_id)
        new_iface = Interface('hostdev')
        new_iface.managed = managed
        if model != "":
            new_iface.model = model
        new_iface.mac_address = utils_net.generate_mac_address_simple()
        new_iface.hostdev_address = new_iface.new_iface_address(**{"attrs": attrs})
        chars = string.ascii_letters + string.digits + '-_'
        alias_name = 'ua-' + ''.join(random.choice(chars) for _ in list(range(64)))
        new_iface.alias = {'name': alias_name}
        if vlan_id:
            new_iface.vlan = new_iface.new_vlan(**vlan_id)
        return new_iface

    def create_vfs(vf_num):
        """
            Create max vfs.
        """
        net_device = []
        net_name = []
        # cleanup env and create vfs
        cmd = "echo 0 > %s/sriov_numvfs" % pci_address
        if driver == "mlx4_core":
            cmd = "modprobe -r mlx4_en ; modprobe -r mlx4_ib ; modprobe -r mlx4_core"
        process.run(cmd, shell=True)
        pci_list = virsh.nodedev_list(cap='pci').stdout.strip().splitlines()
        net_list = virsh.nodedev_list(cap='net').stdout.strip().splitlines()
        pci_list_before = set(pci_list)
        net_list_before = set(net_list)
        cmd = "echo %d > %s/sriov_numvfs" % (vf_num, pci_address)
        if driver == "mlx4_core":
            cmd = "modprobe -v mlx4_core num_vfs=%d port_type_array=2,2 probe_vf=%d" \
                    % (vf_num, vf_num)
        test_res = process.run(cmd, shell=True)
        if test_res.exit_status != 0:
            test.fail("Fail to create vfs")

        def _vf_init_completed():
            try:
                net_list_sriov = virsh.nodedev_list(cap='net').stdout.strip().splitlines()
                net_list_sriov = set(net_list_sriov)
                net_diff = list(net_list_sriov.difference(net_list_before))
                net_count = len(net_diff)
                if ((driver != "mlx4_core" and net_count != vf_num) or
                        (driver == "mlx4_core" and net_count != 2*(vf_num + 1))):
                    net_diff = []
                    return False
                return net_diff
            except process.CmdError:
                raise test.fail("Get net list with 'virsh nodedev-list' failed\n")

        net_diff = utils_misc.wait_for(_vf_init_completed, timeout=300)
        pci_list_sriov = virsh.nodedev_list(cap='pci').stdout.strip().splitlines()
        pci_list_sriov = set(pci_list_sriov)
        pci_diff = list(pci_list_sriov.difference(pci_list_before))
        if not net_diff:
            test.fail("Get net list with 'virsh nodedev-list' failed\n")
        for net in net_diff:
            net = net.split('_')
            length = len(net)
            net = '_'.join(net[1:length-6])
            mac = ':'.join(net[length-6:])
            net_name.append(net)
        for pci_addr in pci_diff:
            temp_addr = pci_addr.split("_")
            pci_addr = ':'.join(temp_addr[1:4]) + '.' + temp_addr[4]
            vf_net_name = os.listdir("%s/%s/net" % (pci_device_dir, pci_addr))[0]
            net_device.append(vf_net_name)
        logging.debug(sorted(net_name))
        logging.debug(sorted(net_device))
        if driver != "mlx4_core" and sorted(net_name) != sorted(net_device):
            test.fail("The net name get from nodedev-list is wrong\n")

    def get_ip_by_mac(mac_addr, timeout=120):
        """
        Get interface IP address by given MAC address.
        """
        if vm.serial_console is not None:
            vm.cleanup_serial_console()
        vm.create_serial_console()
        session = vm.wait_for_serial_login(timeout=240)

        def get_ip():
            return utils_net.get_guest_ip_addr(session, mac_addr)

        try:
            ip_addr = ""
            iface_name = utils_net.get_linux_ifname(session, mac_addr)
            if iface_name is None:
                test.fail("no interface with MAC address %s found" % mac_addr)
            session.cmd("pkill -9 dhclient", ignore_all_errors=True)
            session.cmd("dhclient %s " % iface_name, ignore_all_errors=True)
            ip_addr = utils_misc.wait_for(get_ip, 20)
            logging.debug("The ip addr is %s", ip_addr)
        except Exception:
            logging.warning("Find %s with MAC address %s but no ip for it" % (iface_name, mac_addr))
        finally:
            session.close()
        return ip_addr

    def create_nodedev_pci(pci_address):
        """
            Convert xxxx:xx.x to pci_xxxx_xx_xx_x.
        """
        nodedev_addr = pci_address.split(':')[0:2]
        slot_function = pci_address.split(':')[2]
        nodedev_addr.append(slot_function.split('.')[0])
        nodedev_addr.append(slot_function.split('.')[1])
        nodedev_addr.insert(0, "pci")
        nodedev_addr = "_".join(nodedev_addr)
        return nodedev_addr

    def create_network_interface(name):
        """
            Create network type interface xml.
        """
        new_iface = Interface('network')
        new_iface.source = {'network': name}
        new_iface.model = "virtio"
        new_iface.mac_address = utils_net.generate_mac_address_simple()
        if vlan_id:
            new_iface.vlan = new_iface.new_vlan(**vlan_id)
        return new_iface

    def create_hostdev_network():
        """
            Create hostdev type with vf pool network xml.
        """
        vf_addr_list = []
        netxml = network_xml.NetworkXML()
        if vf_pool_source == "vf_list":
            for vf in vf_list:
                attrs = create_address_dict(vf)
                new_vf = netxml.new_vf_address(**{'attrs': attrs})
                vf_addr_list.append(new_vf)
            netxml.driver = {'name': 'vfio'}
            netxml.forward = {"mode": "hostdev", "managed": managed}
            netxml.vf_list = vf_addr_list
        else:
            netxml.pf = {"dev": pf_name}
            netxml.forward = {"mode": "hostdev", "managed": managed}
        netxml.name = net_name
        logging.debug(netxml)
        return netxml

    def create_macvtap_network():
        """
        Create macvtap type network xml.
        """
        forward_interface_list = []
        for vf_name in vf_name_list:
            forward_interface = {'dev': vf_name}
            forward_interface_list.append(forward_interface)
        netxml = network_xml.NetworkXML()
        netxml.name = net_name
        netxml.forward = {'dev': vf_name_list[0], 'mode': 'passthrough'}
        netxml.forward_interface = forward_interface_list
        logging.debug(netxml)
        return netxml

    def do_operation():
        """
            Do operation in guest os with vf and check the os behavior after operation.
        """
        if operation == "resume_suspend":
            try:
                virsh.suspend(vm.name, debug=True, ignore_status=False)
                virsh.resume(vm.name, debug=True, ignore_statue=False)
                get_ip_by_mac(mac_addr, timeout=120)
            except process.CmdError as detail:
                err_msg = "Suspend-Resume %s with vf failed: %s" % (vm_name, detail)
                test.fail(err_msg)
        if operation == "reboot":
            try:
                if vm.serial_console is not None:
                    vm.cleanup_serial_console()
                    vm.create_serial_console()
                virsh.reboot(vm.name, ignore_status=False)
                get_ip_by_mac(mac_addr, timeout=120)
            except process.CmdError as detail:
                err_msg = "Reboot %s with vf failed: %s" % (vm_name, detail)
                test.fail(err_msg)
        if operation == "save":
            result = virsh.managedsave(vm_name, ignore_status=True, debug=True)
            utils_test.libvirt.check_exit_status(result, expect_error=True)

        if operation == "restart_libvirtd":
            detach_interface()
            utils_libvirtd.libvirtd_restart()
            interface = attach_interface()

    def check_info():
        """
            Check the pf or vf info after create vfs.
        """
        if info_type == "pf_info" or info_type == "vf_order":
            nodedev_pci = create_nodedev_pci(pci_address.split("/")[-1])
            xml = NodedevXML.new_from_dumpxml(nodedev_pci)
            if info_type == "pf_info":
                product_info = xml.cap.product_info
                max_count = xml.max_count
                if pci_info.find(product_info) == -1:
                    test.fail("The product_info show in nodedev-dumpxml is wrong\n")
                if int(max_count) != max_vfs:
                    test.fail("The maxCount show in nodedev-dumpxml is wrong\n")
            if info_type == "vf_order":
                vf_addr_list = xml.cap.virt_functions
                if len(vf_addr_list) != max_vfs:
                    test.fail("The num of vf list show in nodedev-dumpxml is wrong\n")
                addr_list = []
                for vf_addr in vf_addr_list:
                    addr = vf_addr.domain+":"+vf_addr.bus+":"+vf_addr.slot+"."+vf_addr.function
                    addr_list.append(addr)
                logging.debug("The vf addr list show in nodedev-dumpxml is %s\n", addr_list)
                if sorted(addr_list) != addr_list:
                    test.fail("The vf addr list show in nodedev-dumpxml is not sorted correctly\n")
        elif info_type == "vf_info":
            vf_addr = vf_list[0]
            nodedev_pci = create_nodedev_pci(vf_addr)
            vf_xml = NodedevXML.new_from_dumpxml(nodedev_pci)
            vf_bus_slot = ':'.join(vf_addr.split(':')[1:])
            res = process.run("lspci -s %s -vv" % vf_bus_slot)
            vf_pci_info = res.stdout_text
            vf_product_info = vf_xml.cap.product_info
            if vf_pci_info.find(vf_product_info) == -1:
                test.fail("The product_info show in nodedev-dumpxml is wrong\n")
            pf_addr = vf_xml.cap.virt_functions[0]
            pf_addr_domain = re.findall(r"0x(.+)", pf_addr.domain)[0]
            pf_addr_bus = re.findall(r"0x(.+)", pf_addr.bus)[0]
            pf_addr_slot = re.findall(r"0x(.+)", pf_addr.slot)[0]
            pf_addr_function = re.findall(r"0x(.+)", pf_addr.function)[0]
            pf_pci = pf_addr_domain+":"+pf_addr_bus+":"+pf_addr_slot+"."+pf_addr_function
            if pf_pci != pci_id:
                test.fail("The pf address show in vf nodedev-dumpxml is wrong\n")

    def create_interface():
        """
            Call different function to create interface according to the type
        """
        new_iface = Interface('network')
        if vf_type == "vf":
            new_iface = create_hostdev_interface(vf_addr, managed, model)
        if vf_type == "vf_pool":
            netxml = create_hostdev_network()
            virsh.net_define(netxml.xml, ignore_status=True)
            if not inactive_pool:
                virsh.net_start(netxml.name)
            new_iface = create_network_interface(netxml.name)
        if vf_type == "macvtap":
            new_iface = Interface('direct')
            new_iface.source = {"dev": vf_name, "mode": "passthrough"}
            new_iface.mac_address = utils_net.generate_mac_address_simple()
            new_iface.model = "virtio"
            if vlan_id:
                new_iface.vlan = new_iface.new_vlan(**vlan_id)
        if vf_type == "macvtap_network":
            netxml = create_macvtap_network()
            result = virsh.net_define(netxml.xml, ignore_status=True)
            virsh.net_start(netxml.name)
            new_iface = create_network_interface(netxml.name)
        return new_iface

    def detach_interface():
        """
            Detach interface:

            1.Detach interface from xml;
            2.Check the live xml after detach interface;
            3.Check the vf driver after detach interface.
        """
        def _detach_completed():
            result = virsh.domiflist(vm_name, "", ignore_status=True)
            return result.stdout.find(mac_addr) == -1

        def check_addr_attrs():
            live_xml = vm_xml.VMXML.new_from_dumpxml(vm_name)
            device = live_xml.devices
            hostdev_list = device.by_device_tag("hostdev")
            for hostdev in hostdev_list:
                addr = hostdev.source.untyped_address
                hostdev_addr_attrs = {"domain": addr.domain, "bus": addr.bus, "slot": addr.slot, "function": addr.function}
                if hostdev_addr_attrs == vf_addr_attrs:
                    return False
            return True

        result = virsh.detach_device(vm_name, new_iface.xml)
        utils_test.libvirt.check_exit_status(result, expect_error=False)
        if vf_type == "hostdev":
            check_ret = utils_misc.wait_for(check_addr_attrs, timeout=60)
            if not check_ret:
                test.fail("The hostdev device detach failed from xml\n")
        else:
            utils_misc.wait_for(_detach_completed, timeout=60)
        live_xml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        device = live_xml.devices
        logging.debug("Domain xml after detach interface:\n %s", live_xml)

        if vf_type == "vf" or vf_type == "vf_pool":
            for interface in device.by_device_tag("interface"):
                if interface.type_name == "hostdev":
                    if interface.hostdev_address.attrs == vf_addr_attrs:
                        test.fail("The hostdev interface still in the guest xml after detach\n")
                    break
            driver = os.readlink(os.path.join(pci_device_dir, vf_addr, "driver")).split('/')[-1]
            logging.debug("The driver after vf detached from guest is %s\n", driver)
            if managed == "no":
                if driver != "vfio-pci":
                    test.fail("The vf pci driver is not vfio-pci after detached from guest with managed as no\n")
                result = virsh.nodedev_reattach(nodedev_pci_addr)
                utils_test.libvirt.check_exit_status(result, expect_error=False)
            elif driver != origin_driver:
                test.fail("The vf pci driver is not reset to the origin driver after detach from guest: %s vs %s\n" % (driver, origin_driver))
        else:
            for interface in device.by_device_tag("interface"):
                if interface.type_name == "direct":
                    if interface.source["dev"] == vf_name:
                        test.fail("The macvtap interface still exist in the guest xml after detach\n")
                    break

    def attach_interface():
        """
            Attach interface:

            1.Attach interface from xml;
            2.Check the vf driver after attach interface;
            3.Check the live xml after attach interface;
        """
        if managed == "no":
            result = virsh.nodedev_detach(nodedev_pci_addr)
            utils_test.libvirt.check_exit_status(result, expect_error=False)
        logging.debug("attach interface xml:\n %s", new_iface)
        result = virsh.attach_device(vm_name, new_iface.xml, flagstr=option, debug=True)
        utils_test.libvirt.check_exit_status(result, expect_error=False)
        if option == "--config":
            result = virsh.start(vm_name)
            utils_test.libvirt.check_exit_status(result, expect_error=False)
        # For option == "--persistent", after VM destroyed and then start, the device should still be there.
        if option == "--persistent":
            virsh.destroy(vm_name)
            result = virsh.start(vm_name, debug=True)
            utils_test.libvirt.check_exit_status(result, expect_error=False)
        live_xml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        logging.debug(live_xml)

        if vf_type != "hostdev":
            get_ip_by_mac(mac_addr, timeout=60)

        device = live_xml.devices

        if vf_type == "hostdev":
            hostdev_list = device.by_device_tag("hostdev")
            if len(hostdev_list) == 0:
                test.fail("The hostdev device attach failed from xml\n")
            else:
                for hostdev in hostdev_list:
                    if hostdev.type == "pci":
                        break
                interface = hostdev

        if vf_type == "vf" or vf_type == "vf_pool":
            for interface in device.by_device_tag("interface"):
                if interface.type_name == "hostdev":
                    if interface.driver.driver_attr['name'] != 'vfio':
                        test.fail("The driver of the hostdev interface is not vfio\n")
                    break
            vf_addr_attrs = interface.hostdev_address.attrs
            pci_addr = addr_to_pci(vf_addr_attrs)
            nic_driver = os.readlink(os.path.join(pci_device_dir, vf_addr, "driver")).split('/')[-1]
            if nic_driver != "vfio-pci":
                test.fail("The driver of the hostdev interface is not vfio\n")
        elif vf_type == "macvtap" or vf_type == "macvtap_network":
            for interface in device.by_device_tag("interface"):
                if interface.type_name == "direct":
                    if vf_type == "macvtap":
                        if interface.source["dev"] == new_iface.source["dev"]:
                            match = "yes"
                            vf_name = interface.source["dev"]
                    elif interface.source['dev'] in vf_name_list:
                        match = "yes"
                        vf_name = interface.source["dev"]
                if match != "yes":
                    test.fail("The dev name or mode of macvtap interface is wrong after attach\n")
        return interface

    def setup_controller(nic_num, controller_index, ctl_models):
        """
        Create controllers bond to numa node in the guest xml

        :param nic_num: number of nic card bond to numa node
        :param controller_index: index num used to create controllers
        :param ctl_models: contoller topo for numa bond
        """
        index = controller_index
        if nic_num == 2:
            ctl_models.append('pcie-switch-upstream-port')
            ctl_models.append('pcie-switch-downstream-port')
            ctl_models.append('pcie-switch-downstream-port')
        for i in range(index):
            controller = Controller("controller")
            controller.type = "pci"
            controller.index = i
            if i == 0:
                controller.model = 'pcie-root'
            else:
                controller.model = 'pcie-root-port'
            vmxml.add_device(controller)
        set_address = False
        for model in ctl_models:
            controller = Controller("controller")
            controller.type = "pci"
            controller.index = index
            controller.model = model
            if set_address or model == "pcie-switch-upstream-port":
                attrs = {'type': 'pci', 'domain': '0', 'slot': '0',
                         'bus': index - 1, 'function': '0'}
                controller.address = controller.new_controller_address(**{"attrs": attrs})
                logging.debug(controller)
            if controller.model == "pcie-expander-bus":
                controller.node = "0"
                controller.target = {'busNr': '100'}
                set_address = True
            else:
                set_address = False
            logging.debug(controller)
            vmxml.add_device(controller)
            index += 1
        return index - 1

    def add_numa(vmxml):
        """
        Add numa node in the guest xml

        :param vmxml: The instance of VMXML clas
        """
        vcpu = vmxml.vcpu
        max_mem = vmxml.max_mem
        max_mem_unit = vmxml.max_mem_unit
        numa_dict = {}
        numa_dict_list = []
        # Compute the memory size for each numa node
        if vcpu == 1:
            numa_dict['id'] = '0'
            numa_dict['cpus'] = '0'
            numa_dict['memory'] = str(max_mem)
            numa_dict['unit'] = str(max_mem_unit)
            numa_dict_list.append(numa_dict)
        else:
            for index in range(2):
                numa_dict['id'] = str(index)
                numa_dict['memory'] = str(max_mem // 2)
                numa_dict['unit'] = str(max_mem_unit)
                if vcpu == 2:
                    numa_dict['cpus'] = str(index)
                else:
                    if index == 0:
                        if vcpu == 3:
                            numa_dict['cpus'] = str(index)
                        if vcpu > 3:
                            numa_dict['cpus'] = "%s-%s" % (index,
                                                           vcpu // 2 - 1)
                    else:
                        numa_dict['cpus'] = "%s-%s" % (vcpu // 2,
                                                       str(vcpu - 1))
                numa_dict_list.append(numa_dict)
                numa_dict = {}
        # Add cpu device with numa node setting in domain xml
        vmxml_cpu = vm_xml.VMCPUXML()
        vmxml_cpu.xml = "<cpu mode='host-model'><numa/></cpu>"
        vmxml_cpu.numa_cell = vmxml_cpu.dicts_to_cells(numa_dict_list)
        vmxml.cpu = vmxml_cpu

    def create_iface_list(bus_id, nic_num, vf_list):
        """
            Create hostdev interface list bond to numa node

            :param bus_id: bus_id in pci address which decides the controller attached to
            :param nic_num: number of nic card bond to numa node
            :param vf_list: sriov vf list
        """
        iface_list = []
        for num in range(nic_num):
            vf_addr = vf_list[num]
            iface = create_hostdev_interface(vf_addr, managed, model)
            bus_id -= num
            attrs = {'type': 'pci', 'domain': '0', 'slot': '0',
                     'bus': bus_id, 'function': '0'}
            iface.address = iface.new_iface_address(**{"attrs": attrs})
            iface_list.append(iface)
        return iface_list

    def check_guestos(iface_list):
        """
            Check whether vf bond to numa node can get ip successfully in guest os

            :param iface_list: hostdev interface list
        """
        for iface in iface_list:
            mac_addr = iface.mac_address
            get_ip_by_mac(mac_addr, timeout=60)

    def check_numa(vf_driver):
        """
        Check whether vf bond to correct numa node in guest os

        :param vf_driver: vf driver
        """
        if vm.serial_console:
            vm.cleanup_serial_console()
        vm.create_serial_console()
        session = vm.wait_for_serial_login(timeout=240)
        vf_pci = "/sys/bus/pci/drivers/%s" % vf_driver
        vf_dir = session.cmd_output("ls -d %s/00*" % vf_pci).strip().split('\n')
        for vf in vf_dir:
            numa_node = session.cmd_output('cat %s/numa_node' % vf).strip().split('\n')[-1]
            logging.debug("The vf is attached to numa node %s\n", numa_node)
            if numa_node != "0":
                test.fail("The vf is not attached to numa node 0\n")
        session.close()

    def remove_devices(vmxml, device_type):
        """
        Remove all addresses for all devices who has one.

        :param vm_xml: The VM XML to be modified
        :param device_type: The device type for removing

        :return: True if success, otherwise, False
        """
        if device_type not in ['address', 'usb']:
            return
        type_dict = {'address': '/devices/*/address',
                     'usb': '/devices/*'}
        try:
            for elem in vmxml.xmltreefile.findall(type_dict[device_type]):
                if device_type == 'usb':
                    if elem.get('bus') == 'usb':
                        vmxml.xmltreefile.remove(elem)
                else:
                    vmxml.xmltreefile.remove(elem)
        except (AttributeError, TypeError) as details:
            test.error("Fail to remove '%s': %s" % (device_type, details))
        vmxml.xmltreefile.write()

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(params["main_vm"])
    machine_type = params.get("machine_type", "pc")
    operation = params.get("operation")
    driver = params.get("driver", "ixgbe")
    status_error = params.get("status_error", "no") == "yes"
    model = params.get("model", "")
    managed = params.get("managed", "yes")
    attach = params.get("attach", "")
    option = params.get("option", "")
    vf_type = params.get("vf_type", "")
    dev_type = params.get("dev_type", "")
    info_check = params.get("info_check", "no")
    info_type = params.get("info_type", "")
    vf_pool_source = params.get("vf_pool_source", "vf_list")
    loop_times = int(params.get("loop_times", "1"))
    start_vm = "yes" == params.get("start_vm", "yes")
    including_pf = "yes" == params.get("including_pf", "no")
    max_vfs_attached = "yes" == params.get("max_vfs_attached", "no")
    inactive_pool = "yes" == params.get("inactive_pool", "no")
    duplicate_vf = "yes" == params.get("duplicate_vf", "no")
    expected_error = params.get("error_msg", "")
    nic_num = int(params.get("nic_num", "1"))
    nfv = params.get("nfv", "no") == "yes"
    ctl_models = params.get("ctl_models", "").split(' ')
    controller_index = int(params.get("controller_index", "12"))
    vlan_id = eval(params.get("vlan_id", "None"))
    trunk = params.get("trunk", "no") == "yes"

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()
    vmxml.remove_all_device_by_type('interface')
    vmxml.sync()
    if max_vfs_attached:
        controller_devices = vmxml.get_devices("controller")
        pci_bridge_controllers = []
        for device in controller_devices:
            logging.debug(device)
            if device.type == 'pci' and device.model == "pci-bridge":
                pci_bridge_controllers.append(device)
        if not pci_bridge_controllers and machine_type != 'q35':
            pci_bridge_controller = Controller("controller")
            pci_bridge_controller.type = "pci"
            pci_bridge_controller.index = "1"
            pci_bridge_controller.model = "pci-bridge"
            vmxml.add_device(pci_bridge_controller)
            vmxml.sync()

    if start_vm:
        if not vm.is_dead():
            vm.destroy()
        vm.start()
        if vm.serial_console is not None:
            vm.cleanup_serial_console()
        vm.create_serial_console()
        session = vm.wait_for_serial_login(timeout=240)
        session.close()
    else:
        if not vm.is_dead():
            vm.destroy()
    driver_dir = "/sys/bus/pci/drivers/%s" % driver
    pci_dirs = glob.glob("%s/000*" % driver_dir)
    pci_device_dir = "/sys/bus/pci/devices"
    pci_address = ""
    net_name = "test-net"

    # Prepare interface xml
    try:
        pf_iface_name = ""
        pci_address = utils_misc.wait_for(find_pf, timeout=60)
        if not pci_address:
            test.cancel("no up pf found in the test machine")
        pci_id = pci_address.split("/")[-1]
        pf_name = os.listdir('%s/net' % pci_address)[0]
        bus_slot = ':'.join(pci_address.split(':')[1:])
        if not utils_package.package_install('pciutils'):
            test.error('Failed to install "pciutils" which provides '
                       'command "lspci"')
        pci_info = process.run("lspci -s %s -vv" % bus_slot).stdout_text
        logging.debug("The pci info of the sriov card is:\n %s", pci_info)
        max_vfs = int(re.findall(r"Total VFs: (.+?),", pci_info)[0]) - 1
        if info_check == 'yes' or max_vfs < 32:
            vf_num = max_vfs
            create_vfs(vf_num)
        else:
            vf_num = int(max_vfs // 2 + 1)
            create_vfs(vf_num)

        vf_list = []
        vf_name_list = []
        vf_mac_list = []

        for i in range(vf_num):
            vf = os.readlink("%s/virtfn%s" % (pci_address, str(i)))
            vf = os.path.split(vf)[1]
            vf_list.append(vf)
            vf_name = os.listdir('%s/%s/net' % (pci_device_dir, vf))[0]
            with open('%s/%s/net/%s/address' % (pci_device_dir, vf, vf_name), 'r') as f:
                vf_mac = f.readline().strip()
            vf_name_list.append(vf_name)
            vf_mac_list.append(vf_mac)

        if attach == "yes" and not nfv:
            vf_addr = vf_list[0]
            if dev_type:
                mac_addr = vf_mac_list[0]
                new_iface = utils_test.libvirt.create_hostdev_xml(vf_addr,
                                                                  managed=managed)
            else:
                new_iface = create_interface()
                mac_addr = new_iface.mac_address
            if inactive_pool or trunk:
                result = virsh.attach_device(vm_name, file_opt=new_iface.xml, flagstr=option,
                                             ignore_status=True, debug=True)
                utils_test.libvirt.check_result(result, expected_error)
            else:
                nodedev_pci_addr = create_nodedev_pci(vf_addr)
                origin_driver = os.readlink(os.path.join(pci_device_dir, vf_addr, "driver")).split('/')[-1]
                logging.debug("The driver of vf before attaching to guest is %s\n", origin_driver)
                count = 0
                while count < loop_times:
                    interface = attach_interface()
                    if vf_type in ["vf", "vf_pool"]:
                        vf_addr_attrs = interface.hostdev_address.attrs
                    if vf_type == "hostdev":
                        addr = interface.source.untyped_address
                        vf_addr_attrs = {"domain": addr.domain, "bus": addr.bus,
                                         "slot": addr.slot, "function": addr.function}
                    if operation != "":
                        do_operation()
                    detach_interface()
                    count += 1
                if max_vfs_attached:
                    interface_list = []
                    # Get max index of all pcie-root-port
                    pcie_ctls = vmxml.get_controllers('pci', 'pcie-root-port')
                    pcie_indexes = [int(port.get('index')) for port in pcie_ctls]

                    def _add_pcie_root_port(index):
                        """
                        Add pcie root port with given index
                        :param index: index of port that is going to be added
                        :return:
                        """
                        pcie_root_port = Controller("controller")
                        pcie_root_port.type = "pci"
                        pcie_root_port.index = index
                        pcie_root_port.model = "pcie-root-port"
                        vmxml.add_device(pcie_root_port)
                        vmxml.sync()

                    for i in range(len(vf_list)):
                        _add_pcie_root_port(max(pcie_indexes) + 1 + i)
                    vm.start()
                    for vf_addr in vf_list:
                        new_iface = create_interface()
                        mac_addr = new_iface.mac_address
                        nodedev_pci_addr = create_nodedev_pci(vf_addr)
                        attach_interface()
                        interface_list.append(new_iface)
                    count = 0
                    for new_iface in interface_list:
                        vf_addr = vf_list[count]
                        vf_addr_attrs = new_iface.hostdev_address.attrs
                        detach_interface()
                        count += 1
        if info_check == "yes":
            check_info()
        if including_pf:
            vf_list = []
            pf_addr = pci_id
            vf_list.append(pf_addr)
            netxml = create_hostdev_network()
            result = virsh.net_define(netxml.xml, ignore_status=True, debug=True)
            utils_test.libvirt.check_result(result, expected_error)
        if duplicate_vf:
            vf_list.append(vf_list[0])
            netxml = create_hostdev_network()
            result = virsh.net_define(netxml.xml, ignore_status=True, debug=True)
            utils_test.libvirt.check_result(result, expected_error)
            result = virsh.net_create(netxml.xml, ignore_status=True, debug=True)
            utils_test.libvirt.check_result(result, expected_error)
        if nfv:
            vf_driver = os.readlink(os.path.join(pci_device_dir, vf_list[0], "driver")).split('/')[-1]
            vmxml.remove_all_device_by_type('controller')
            remove_devices(vmxml, 'address')
            remove_devices(vmxml, 'usb')
            osxml = vmxml.os
            if "i440fx" in vmxml.os.machine:
                osxml.machine = "q35"
                vmxml.os = osxml
            add_numa(vmxml)
            bus_id = setup_controller(nic_num, controller_index, ctl_models)
            vmxml.sync()
            logging.debug(vmxml)
            iface_list = create_iface_list(bus_id, nic_num, vf_list)
            for iface in iface_list:
                txt = process.run("cat %s" % iface.xml, shell=True).stdout_text
                logging.debug("iface_xml for attach device is %s" % txt)
                result = virsh.attach_device(vm_name, file_opt=iface.xml, flagstr=option,
                                             ignore_status=True, debug=True)
                utils_test.libvirt.check_exit_status(result, expect_error=False)
            result = virsh.start(vm_name, debug=True)
            utils_test.libvirt.check_exit_status(result, expect_error=False)
            live_xml = vm_xml.VMXML.new_from_dumpxml(vm_name)
            logging.debug(live_xml)
            check_guestos(iface_list)
            check_numa(vf_driver)
    finally:
        if vm.is_alive():
            vm.destroy(gracefully=False)
        backup_xml.sync()
        if driver == "mlx4_core":
            # Reload mlx4 driver to default setting
            process.run("modprobe -r mlx4_en ; modprobe -r mlx4_ib ; modprobe -r mlx4_core", shell=True)
            process.run("modprobe mlx4_core; modprobe mlx4_ib;  modprobe mlx4_en", shell=True)
        else:
            process.run("echo 0 > %s/sriov_numvfs" % pci_address, shell=True)
        if vf_type == "vf_pool" or vf_type == "macvtap_network":
            virsh.net_destroy(net_name)
            virsh.net_undefine(net_name, ignore_status=True)
