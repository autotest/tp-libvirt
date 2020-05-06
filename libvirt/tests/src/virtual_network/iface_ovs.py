import logging
import shutil

from avocado.utils import distro
from avocado.utils import process

from virttest import virt_vm
from virttest import virsh
from virttest import utils_net
from virttest import utils_package
from virttest.staging import service
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.network_xml import NetworkXML


def run(test, params, env):
    """
    Test openvswitch support for network.

    1.Prepare test environment,destroy or suspend a VM.
    2.Edit xml and start the domain.
    3.Perform test operation.
    4.Recover test environment.
    5.Confirm the test result.
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    # install openvswitch on host.
    if distro.detect().name == 'Ubuntu':
        pkg = "openvswitch-switch"
    else:
        pkg = "openvswitch"

    ovs_service = service.Factory.create_service(pkg)

    if not shutil.which('ovs-vsctl') and not utils_package.package_install(pkg):
        test.cancel("Failed to install dependency package %s"
                    " on host" % pkg)
    if not ovs_service.status():
        logging.debug("Restart %s service.." % pkg)
        ovs_service.restart()

    def modify_iface_xml():
        """
        Modify interface xml options
        """
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        xml_devices = vmxml.devices
        iface_index = xml_devices.index(
            xml_devices.by_device_tag("interface")[0])
        iface = xml_devices[iface_index]

        iface_type = params.get("iface_type")
        if iface_type:
            iface.type_name = iface_type
        source = eval(iface_source)
        if source:
            del iface.source
            iface.source = source
        iface_model = params.get("iface_model", "virtio")
        iface.model = iface_model
        iface_virtualport = params.get("iface_virtualport")
        if iface_virtualport:
            iface.virtualport_type = iface_virtualport
        logging.debug("New interface xml file: %s", iface)
        vmxml.devices = xml_devices
        vmxml.xmltreefile.write()
        vmxml.sync()

    def check_ovs_port(ifname, brname):
        """
        Check OVS port that created by libvirt
        """
        pg_name = params.get("porgroup_name", "").split()
        pg_vlan = params.get("portgroup_vlan", "").split()
        if_source = eval(iface_source)
        port_vlan = {}
        if "portgroup" in if_source:
            pg = if_source["portgroup"]
            for (name, vlan) in zip(pg_name, pg_vlan):
                if pg == name:
                    port_vlan = eval(vlan)
        # Check bridge name by port name
        _, bridge = utils_net.find_current_bridge(ifname)
        assert bridge == brname
        # Get port info from ovs-vsctl output
        cmd = "ovs-vsctl list port %s" % ifname
        output = process.run(cmd, shell=True).stdout_text
        logging.debug("ovs port output: %s", output)
        for line in output.splitlines():
            if line.count("tag"):
                tag_info = line.rsplit(':')
                if ("id" in port_vlan and
                        tag_info[0] == "tag"):
                    assert port_vlan["id"] == tag_info[1]
            elif line.count("vlan_mode"):
                mode_info = line.rsplit(':')
                if ("nativeMode" in port_vlan and
                        mode_info[0] == "vlan_mode"):
                    assert (port_vlan["nativeMode"] ==
                            "native-%s" % mode_info[1])

    start_error = "yes" == params.get("start_error", "no")

    # network specific attributes.
    net_name = params.get("net_name", "default")
    net_bridge = params.get("net_bridge", "{'name':'virbr0'}")
    iface_source = params.get("iface_source", "{}")
    create_network = "yes" == params.get("create_network", "no")
    change_iface_option = "yes" == params.get("change_iface_option", "no")
    test_ovs_port = "yes" == params.get("test_ovs_port", "no")

    # Destroy the guest first
    if vm.is_alive():
        vm.destroy(gracefully=False)

    # Back up xml file.
    netxml_backup = NetworkXML.new_from_net_dumpxml("default")
    iface_mac = vm_xml.VMXML.get_first_mac_by_name(vm_name)
    params["guest_mac"] = iface_mac
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    bridge_name = eval(net_bridge)['name']
    # Build the xml and run test.
    try:
        # Edit the network xml or create a new one.
        if create_network:
            # Try to add ovs bridge first
            if not utils_net.ovs_br_exists(bridge_name):
                utils_net.add_ovs_bridge(bridge_name)
            netxml = libvirt.create_net_xml(net_name, params)
            netxml.sync()
        # Edit the interface xml.
        if change_iface_option:
            # Try to add bridge if needed
            source = eval(iface_source)
            if source:
                if "bridge" in source:
                    if not utils_net.ovs_br_exists(source["bridge"]):
                        utils_net.add_ovs_bridge(source["bridge"])
            modify_iface_xml()

        try:
            # Start the VM.
            vm.start()
            if start_error:
                test.fail("VM started unexpectedly")

            iface_name = libvirt.get_ifname_host(vm_name, iface_mac)
            if test_ovs_port:
                check_ovs_port(iface_name, bridge_name)

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
        # Try to recovery ovs bridge
        if utils_net.ovs_br_exists(bridge_name):
            utils_net.del_ovs_bridge(bridge_name)
        vmxml_backup.sync()
