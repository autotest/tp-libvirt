import os
import logging
import re
import tempfile
from autotest.client.shared import error
from virttest import data_dir
from virttest.libvirt_xml import network_xml, xcepts
from virttest import virsh


def run(test, params, env):

    net_name = params.get("net_update_net_name", "updatenet")
    net_section = params.get("network_section", "ip-dhcp-range")
    update_command = params.get("update_command", "add-last")
    options = params.get("cmd_options", "")
    net_state = params.get("net_state")

    virtual_net = """
<network>
  <name>%s</name>
  <forward mode='nat'/>
  <bridge name='%s' stp='on' delay='0' />
  <mac address='52:54:00:03:78:6c'/>
  <ip address='192.168.100.1' netmask='255.255.255.0'>
    <dhcp>
      <range start='192.168.100.2' end='192.168.100.254' />
      <host mac='52:54:00:5a:a0:8b' ip='192.168.100.152' />
    </dhcp>
  </ip>
</network>
""" % (net_name, net_name)

    port_group = """
<portgroup name='engineering' default='no'>
  <virtualport type='802.1Qbh'>
    <parameters profileid='test'/>
  </virtualport>
  <bandwidth>
    <inbound average='1000' peak='5000' burst='5120'/>
    <outbound average='1000' peak='5000' burst='5120'/>
  </bandwidth>
</portgroup>
"""
    try:
        test_xml = network_xml.NetworkXML(network_name=net_name)
        test_xml.xml = virtual_net
        if net_section == "portgroup":
            portgroup_xml = network_xml.PortgroupXML()
            portgroup_xml.xml = port_group
            test_xml.portgroup = portgroup_xml
        elif net_section == "forward-interface":
            test_xml.forward = {'mode': 'passthrough'}
            test_xml.forward_interface = [{'dev': 'eth2'}]
            del test_xml.bridge
            del test_xml.mac
            del test_xml.ip
        test_xml.define()
        if net_state == "active":
            test_xml.start()
        test_xml.debug_xml()
        virsh.net_dumpxml(net_name)
    except xcepts.LibvirtXMLError, detail:
        raise error.TestError("Failed to define a test network.\n"
                              "Detail: %s." % detail)

    try:
        # Get a tmp_dir.
        tmp_dir = data_dir.get_tmp_dir()

        # Write new xml into a tempfile
        tmp_file = tempfile.NamedTemporaryFile(prefix=("new_xml_"),
                                               dir=tmp_dir)
        xmlfile = tmp_file.name
        tmp_file.close()

        element = "/%s" % net_section.replace('-', '/')
        section_xml = test_xml.get_section_string(xpath=element)

        flag_list = []
        if update_command == "delete":
            newxml = section_xml
        else:
            if net_section == "bridge":
                new_bridge_name = net_name + "_new"
                flag_list.append(new_bridge_name)
                newxml = section_xml.replace(net_name, new_bridge_name)
                logging.info("The new bridge xml is %s", newxml)

            elif net_section == "forward":
                new_mode = "route"
                flag_list.append(new_mode)
                newxml = section_xml.replace("nat", new_mode)
                logging.info("The new forward xml is %s", newxml)

            elif net_section == "ip":
                new_netmask = "255.255.0.0"
                flag_list.append(new_netmask)
                newxml = section_xml.replace("255.255.255.0", new_netmask)
                logging.info("The new xml of ip section is %s", newxml)

            elif net_section == "ip-dhcp-range":
                new_end_ip = "192.168.100.253"
                flag_list.append(new_end_ip)
                newxml = section_xml.replace("192.168.100.254", new_end_ip)
                logging.info("The new xml of ip-dhcp-range is %s", newxml)
            elif net_section == "portgroup":
                new_inbound_average = "2000"
                flag_list.append(new_inbound_average)
                newxml = section_xml.replace("1000", new_inbound_average)
                newxml = newxml.replace("no", "yes")
                flag_list.append("yes")
                if update_command in ['add-first', 'add-last']:
                    newxml = newxml.replace("engineering", "sales")
                    flag_list.append("sales")
                logging.info("The new xml of portgroup is %s", newxml)
            elif net_section == "forward-interface":
                new_forward_iface = params.get("new_forward_iface", "eth4")
                flag_list.append(new_forward_iface)
                newxml = section_xml.replace("eth2", new_forward_iface)
                logging.info("The new xml of forward-interface is %s", newxml)
            elif net_section == "ip-dhcp-host":
                new_ip_dhcp_host = params.get("new_ip_dhcp_host",
                                              "192.168.100.153")
                flag_list.append(new_ip_dhcp_host)
                newxml = section_xml.replace("192.168.100.152",
                                             new_ip_dhcp_host)
                if update_command in ['add-first', 'add-last']:
                    new_ip_dhcp_mac = params.get("new_ip_dhcp_mac",
                                                 "52:54:00:5a:a0:9b")
                    newxml = newxml.replace("52:54:00:5a:a0:8b",
                                            new_ip_dhcp_mac)
                    flag_list.append(new_ip_dhcp_mac)
                logging.info("The new xml of forward-interface is %s", newxml)
            else:
                raise error.TestFail("Unknown network section")

        fd = open(xmlfile, 'w')
        fd.write(newxml)
        fd.close()

        cmd_result = virsh.net_update(net_name,
                                      update_command,
                                      net_section,
                                      xmlfile, options, debug=True)

        if cmd_result.exit_status:
            err = cmd_result.stderr.strip()
            if re.search("is not supported", err):
                raise error.TestNAError("Skip the test: %s" % err)
            else:
                raise error.TestFail("Failed to execute "
                                     "virsh net-update command")

        # Check the actual xml
        virsh_option = ""
        if options == "--config":
            virsh_option = "--inactive"
        cmd_result = virsh.net_dumpxml(net_name, virsh_option)
        actual_net_xml = cmd_result.stdout.strip()
        logging.info("After net-update, the actual net xml is %s",
                     actual_net_xml)

        if update_command == "delete":
            new_xml_obj = network_xml.NetworkXML.new_from_net_dumpxml(
                net_name, extra=virsh_option)
            try:
                section_str = new_xml_obj.get_section_string(xpath=element)
            except xcepts.LibvirtXMLNotFoundError:
                section_str = None
            if section_str is not None:
                raise error.TestFail("The actual net xml is not expected,"
                                     "element still exists")

        else:
            for flag_string in flag_list:
                if not re.search(flag_string, actual_net_xml):
                    raise error.TestFail("The actual net xml failed to update")

    finally:
        test_xml.undefine()

        if os.path.exists(xmlfile):
            os.remove(xmlfile)
