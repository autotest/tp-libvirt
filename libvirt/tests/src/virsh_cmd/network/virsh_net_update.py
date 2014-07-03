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

    virtual_net = """
<network>
  <name>%s</name>
  <forward mode='nat'/>
  <bridge name='%s' stp='on' delay='0' />
  <mac address='52:54:00:03:78:6c'/>
  <ip address='192.168.100.1' netmask='255.255.255.0'>
    <dhcp>
      <range start='192.168.100.2' end='192.168.100.254' />
    </dhcp>
  </ip>
</network>
""" % (net_name, net_name)

    try:
        test_xml = network_xml.NetworkXML(network_name=net_name)
        test_xml.xml = virtual_net
        test_xml.sync()
    except xcepts.LibvirtXMLError, detail:
        raise error.TestNAError("Failed to define a test network.\n"
                                "Detail: %s." % detail)

    try:
        # Get a tmp_dir.
        tmp_dir = data_dir.get_tmp_dir()

        # Write new xml into a tempfile
        tmp_file = tempfile.NamedTemporaryFile(prefix=("new_xml_"),
                                               dir=tmp_dir)
        xmlfile = tmp_file.name
        tmp_file.close()

        if net_section == "ip-dhcp-range":
            element = "/ip/dhcp/range"
        else:
            element = "/%s" % net_section

        section_xml = test_xml.get_section_string(xpath=element)

        if update_command == "delete":
            newxml = section_xml
        else:
            if net_section == "bridge":
                flag_string = new_bridge_name = net_name + "_new"
                newxml = section_xml.replace(net_name, new_bridge_name)
                logging.info("The new bridge xml is %s", newxml)

            elif net_section == "forward":
                flag_string = new_mode = "route"
                newxml = section_xml.replace("nat", new_mode)
                logging.info("The new forward xml is %s", newxml)

            elif net_section == "ip":
                flag_string = new_netmask = "255.255.0.0"
                newxml = section_xml.replace("255.255.255.0", new_netmask)
                logging.info("The new xml of ip section is %s", newxml)

            elif net_section == "ip-dhcp-range":
                flag_string = new_end_ip = "192.168.100.253"
                newxml = section_xml.replace("192.168.100.254", new_end_ip)
                logging.info("The new xml of ip-dhcp-range is %s", newxml)

            else:
                raise error.TestFail("Unknown network section")

        fd = open(xmlfile, 'w')
        fd.write(newxml)
        fd.close()

        cmd_result = virsh.net_update(net_name,
                                      update_command,
                                      net_section,
                                      xmlfile, debug=True)

        if cmd_result.exit_status:
            err = cmd_result.stderr.strip()
            if re.search("is not supported", err):
                raise error.TestNAError("Skip the test: %s" % err)
            else:
                raise error.TestFail("Failed to execute "
                                     "virsh net-update command")

        # Check the actual xml
        cmd_result = virsh.net_dumpxml(net_name)
        actual_net_xml = cmd_result.stdout.strip()
        logging.info("After net-update, the actual net xml is %s",
                     actual_net_xml)

        if update_command == "delete":
            new_xml_obj = network_xml.NetworkXML.new_from_net_dumpxml(net_name)
            try:
                section_str = new_xml_obj.get_section_string(xpath=element)
            except xcepts.LibvirtXMLNotFoundError:
                section_str = None
            if section_str is not None:
                raise error.TestFail("The actual net xml is not expected,"
                                     "element still exists")

        else:
            if not re.search(flag_string, actual_net_xml):
                raise error.TestFail("The actual net xml failed to update")

    finally:
        test_xml.undefine()

        if os.path.exists(xmlfile):
            os.remove(xmlfile)
