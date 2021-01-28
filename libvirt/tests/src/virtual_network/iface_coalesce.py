import logging
import time
import re


from virttest import virt_vm
from virttest import virsh
from virttest import utils_net
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml, xcepts
from virttest.libvirt_xml.network_xml import NetworkXML
from virttest.libvirt_xml.devices.interface import Interface
from avocado.utils import process

from virttest import libvirt_version


def run(test, params, env):
    """
    Since 3.3.0, Coalesce setting is supported.

    This case is to set coalesce and check for 4 network types and each guest interface type.

    Only network/bridge guest interface type take effect for setting interface coalesce.

    For each host network type, guest can use bridge/network interface type to set coalesce
    except macvtap network type.
    Execute 'ethtool -c ${interface}|grep "rx-frames"' and anylize the output to check
    whether coalesce setting take effect or not.

    For macvtap network type, guest can start but query coalesce will fail.

    1. For default host network

    network definition is below:
    <network>
       <name>default</name>
       <bridge name="virbr0"/>
       <forward/>
       <ip address="192.168.122.1" netmask="255.255.255.0">
          <dhcp>
             <range start="192.168.122.2" end="192.168.122.254"/>
          </dhcp>
       </ip>
    </network>

    This is default network.

    1) guest interface type 'bridge' and set coalesce:
    <interface type='bridge'>
      <mac address='52:54:00:a7:4d:f7'/>
      <source bridge='virbr0'/>
      <target dev='vnet0'/>
      <model type='virtio'/>
      <coalesce>
        <rx>
          <frames max='64'/>
        </rx>
      </coalesce>
      <alias name='net0'/>
      <address type='pci' domain='0x0000' bus='0x00' slot='0x03' function='0x0'/>
    </interface>

    2) guest interface type 'network' and set coalesce:
    <interface type='network'>
      <source network='default'/>
      <model type='virtio'/>
      <coalesce>
        <rx>
          <frames max='32'/>
        </rx>
      </coalesce>
    </interface>

    2. For bridge host network
    This mode need true bridge in host network.
    'nmcli con add type bridge con-name br0 ifname br0'

    1) guest interface type 'bridge' and set coalesce:
    <interface type='bridge'>
      <mac address='52:54:00:8e:f3:6f'/>
      <source bridge='br0'/>
      <target dev='vnet0'/>
      <model type='virtio'/>
      <coalesce>
        <rx>
          <frames max='64'/>
        </rx>
      </coalesce>
      <alias name='net0'/>
      <address type='pci' domain='0x0000' bus='0x00' slot='0x0a' function='0x0'/>
    </interface>

    2) guest interface type 'network' and set coalesce:
    First, define one virtual network for bridge br0
    'virsh net-define net-br0.xml'
    'virsh net-dumpxml net-br0'
    <network>
       <name>net-br0</name>
       <forward mode='bridge'/>
       <bridge name='br0'/>
    </network>
    Secondly, use this network for guest
    <interface type='network'>
      <source network='net-br0'/>
      <model type='virtio'/>
      <coalesce>
        <rx>
          <frames max='32'/>
        </rx>
      </coalesce>
    </interface>

    3. For openvswitch bridge host network
    This mode need true openvswitch bridge in host network.
    'ovs-vsctl add-br ovsbr0'

    1) guest interface type 'bridge' and set coalesce:
    <interface type='bridge'>
      <mac address='52:54:00:8e:f3:6f'/>
      <source bridge='ovsbr0'/>
      <virtualport type='openvswitch'/>
      <target dev='vnet0'/>
      <model type='virtio'/>
      <coalesce>
        <rx>
          <frames max='64'/>
        </rx>
      </coalesce>
      <alias name='net0'/>
      <address type='pci' domain='0x0000' bus='0x00' slot='0x0a' function='0x0'/>
    </interface>

    2) guest interface type 'network' and set coalesce:
    First, define one virtual network for openvswitch bridge ovsbr0
    'virsh net-define net-ovsbr0.xml'
    'virsh net-dumpxml net-ovsbr0'
    <network>
       <name>net-ovs0</name>
       <forward mode='bridge'/>
       <bridge name='ovsbr0'/>
       <virtualport type='openvswitch'/>
    </network>

    Secondly, use this network for guest
    <interface type='network'>
      <source network='net-ovs0'/>
      <model type='virtio'/>
      <coalesce>
        <rx>
          <frames max='32'/>
        </rx>
      </coalesce>
    </interface>

    4. For macvtap bridge mode on host network
    For this mode, first, create one virtual network.
    Note, should set dev to one ture physical interface.
    'virsh net-define net-br-macvtap.xml'
    'virsh net-dumpxml net-br-macvtap'
    <network>
       <name>net-br-macvtap</name>
       <forward dev='eno1' mode='bridge'>
           <interface dev='eno1'/>
       </forward>
    </network>
    Set guest to use this macvtap network and set coalesc
    <interface type='network'>
      <mac address='52:54:00:6e:f4:f1'/>
      <source network='net-br-macvtap'/>
      <model type='virtio'/>
      <coalesce>
        <rx>
          <frames max='32'/>
        </rx>
      </coalesce>
      <address type='pci' domain='0x0000' bus='0x00' slot='0x03' function='0x0'/>
    </interface>

    Steps in this test:
    1. Prepare test environment,destroy or suspend a VM.
    2. Prepare network if necessary.
    3. Edit guest interface with definite network and set coalesce.
    4. Start guest and check whether coalesce setting take effect.
    5. Recover network and guest.
    """

    if not libvirt_version.version_compare(3, 3, 0):
        test.skip("Coalesce setting is only supported by libvirt3.3.0 and above")

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    def get_first_phy_iface():
        """
        Get first physical network interface from output of 'ls /sys/class/net'

        #ls /sys/class/net
         eno1  lo  virbr0  virbr0-nic

        :return: interface name
        """
        interface = ''
        lines = process.run('ls /sys/class/net').stdout_text.splitlines()
        interfaces = lines[0].split()
        for iface in interfaces:
            if iface != 'lo' and 'vir' not in iface:
                interface = iface
                break
        if interface == '':
            test.fail("There is no physical network interface")
        return interface

    def modify_iface_xml():
        """
        Modify interface xml options
        Two methods to modify domain interfae:
        1. modify guest xml, define it
        2. attach one interface for running guest

        :return: 0 for successful negative case
                 test.fail is fail for positive/negative case
                 None for successful positive case
        """
        if hotplug_iface:
            iface = Interface(iface_type)
        else:
            vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
            xml_devices = vmxml.devices
            iface_index = xml_devices.index(
                xml_devices.by_device_tag("interface")[0])
            iface = xml_devices[iface_index]

        if iface_type == 'network':
            iface.type_name = iface_type
            source = {iface_type: net_name}
        elif iface_type == 'bridge' and bridge_name:
            iface.type_name = iface_type
            source = {iface_type: bridge_name}
        elif iface_type == 'direct':
            iface.type_name = iface_type
            source = {'dev': interface, 'mode': 'bridge'}

        if source:
            del iface.source
            iface.source = source
        iface_model = params.get("iface_model", "virtio")
        iface.model = iface_model
        iface.coalesce = {'max': coalesce_value}
        if network_type == "ovsbridge" and iface_type == "bridge":
            iface.virtualport_type = "openvswitch"

        if not hotplug_iface:
            vmxml.devices = xml_devices
            vmxml.xmltreefile.write()
            try:
                vmxml.sync()
            except xcepts.LibvirtXMLError as details:
                if status_error:
                    # Expect error for negetive test
                    return 0
                else:
                    test.fail("Define guest: FAIL")
        else:
            if not vm.is_alive():
                vm.start()
                # Wait guest boot completely
                time.sleep(2)
            try:
                ret = virsh.attach_device(vm_name, iface.xml,
                                          ignore_status=False,
                                          debug=True)
            except process.CmdError as error:
                if status_error:
                    # Expect error for negetive test
                    return 0
                else:
                    test.fail("Define guest: FAIL")

    start_error = "yes" == params.get("start_error", "no")
    status_error = "yes" == params.get("status_error", "no")

    # Get coalesce value.
    expect_coalesce = params.get("expect_coalesce", "")
    coalesce_value = params.get("coalesce", "15")
    if expect_coalesce == '':
        expect_coalesce = coalesce_value

    # Network specific attributes.
    network_type = params.get("network_type", "default")
    net_name = params.get("net_name", "default")
    net_bridge = params.get("net_bridge", "{'name':'virbr0'}")

    # Get guest interface type
    iface_type = params.get("iface_type", "network")

    # Whether attach interface
    hotplug_iface = "yes" == params.get("hotplug_iface", "no")
    error_info = params.get("error_info", "")

    # Destroy the guest first
    if vm.is_alive():
        vm.destroy(gracefully=False)

    # Back up xml file.
    netxml_backup = NetworkXML.new_from_net_dumpxml("default")
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    # Build the xml and run test.
    try:
        interface = get_first_phy_iface()

        # Prepare network for specific network type.
        # Create bridge/ovsbridge for host bridge/ovsbridge network type
        if network_type == "default" and iface_type == "bridge":
            bridge_name = "virbr0"
        elif network_type == "bridge":
            bridge_name = eval(net_bridge)['name']
            bridge = utils_net.Bridge()
            # Try to add bridge if not exist
            if bridge_name not in bridge.list_br():
                bridge.add_bridge(bridge_name)
        elif network_type == 'ovsbridge':
            bridge_name = eval(net_bridge)['name']
            # Try to add ovs bridge if not exist
            if not utils_net.ovs_br_exists(bridge_name):
                utils_net.add_ovs_bridge(bridge_name)

        if iface_type == "network":
            # Define virtual network if not exist for 'network' type of guest interface
            network = NetworkXML()
            network.name = net_name
            # Prepare virtual network required parameters
            params['net_forward'] = "{'mode':'bridge'}"
            if network_type == "ovsbridge":
                params['net_virtualport'] = "openvswitch"
            if network_type == "macvtap":
                # For bridge type of macvtap network, one true physical interface shold be added
                # Check whether physical interface has been added into one bridge. if yes, skip macvtap test
                # If interface already added to a bridge, the output of the nmcli
                # command will include "connection.slave-type: bridge"
                out = process.run('nmcli dev show %s' % interface).stdout_text
                con_l = re.findall(r'GENERAL.CONNECTION:(.+?)\n', out)
                if not con_l:
                    test.cancel("no connection for the interface")
                else:
                    con = con_l[0].strip()
                if "bridge" not in process.run('nmcli con show "%s"' % con).stdout_text:
                    params['forward_iface'] = interface
                    params['net_forward'] = "{'mode':'bridge', 'dev': '%s'}" % interface
                else:
                    test.cancel("interface %s has been added into one brige, but macvtap"
                                "need also add this interface, so current network can't"
                                "suit macvtap testing" % interface)
            if not network.exists():
                netxml = libvirt.create_net_xml(net_name, params)
                netxml.define()
                netxml.start()
                virsh.net_dumpxml(network.name, debug=True)
        # Edit the interface xml.
        # For successful negative case, return 0 to specify PASS result directly
        ret = modify_iface_xml()
        if ret == 0:
            return 0
        try:
            # Get all interface
            link_before = set(process.run('ls /sys/class/net').stdout_text.splitlines())
            # Start the VM.
            vm.start()
            if start_error:
                raise test.fail("VM started unexpectedly")
            # Get guest virtual network interface
            link_after = set(process.run('ls /sys/class/net').stdout_text.splitlines())
            newinterface = (link_after - link_before).pop()
            out = process.run('ethtool -c %s' % newinterface, ignore_status=True)
            if network_type == 'macvtap':
                # Currently, output coalesce for macvtap is not supported
                err_msg = "Operation not supported"
                logging.info("err_msg is %s, out.stderr_text is %s" % (err_msg, out.stderr_text))
                if err_msg not in out.stderr_text:
                    test.fail("coalesce setting on %s failed." % network_type)
            else:
                # Get coalesce value and check it is true
                if out.exit_status == 0:
                    for line in out.stdout_text.splitlines():
                        if 'rx-frames:' in line:
                            coalesce = line.split(':')[1].strip()
                            if expect_coalesce != coalesce:
                                test.fail("coalesce setting failed for %s" % network_type)
                            break
                else:
                    test.fail("coalesce setting on %s failed." % network_type)
        except virt_vm.VMStartError as details:
            logging.info(str(details))
            if not start_error:
                test.fail('VM failed to start:\n%s' % details)

    finally:
        # Recover VM.
        if vm.is_alive():
            vm.destroy(gracefully=False)
        logging.info("Restoring network...")
        if net_name == "default":
            netxml_backup.sync()
        else:
            # Destroy and undefine new created network
            virsh.net_destroy(net_name)
            virsh.net_undefine(net_name)
        # Try to recovery bridge
        if network_type == "bridge" and bridge_name:
            if bridge_name in bridge.list_br():
                bridge.del_bridge(bridge_name)
        elif network_type == "ovsbridge" and bridge_name:
            if utils_net.ovs_br_exists(bridge_name):
                utils_net.del_ovs_bridge(bridge_name)
        vmxml_backup.sync()
