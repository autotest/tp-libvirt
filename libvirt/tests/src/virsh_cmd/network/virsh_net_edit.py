import logging
import re
from virttest.libvirt_xml import network_xml, xcepts
from virttest import aexpect
from virttest import remote
from autotest.client.shared import error
from virttest import virsh


def run(test, params, env):
    """
    Test command: virsh net-edit <network>

    1) Define a temp virtual network
    2) Execute virsh net-edit to modify it
    3) Dump its xml then check it
    """

    def edit_net_xml():
        edit_cmd = r":%s /100.254/100.253"

        session = aexpect.ShellSession("sudo -s")
        try:
            logging.info("Execute virsh net-edit %s", net_name)
            session.sendline("virsh net-edit %s" % net_name)

            logging.info("Change the ip value of dhcp end")
            session.sendline(edit_cmd)
            session.send('\x1b')
            session.send('ZZ')
            remote.handle_prompts(session, None, None, r"[\#\$]\s*$")
            session.close()
        except (aexpect.ShellError, aexpect.ExpectError), details:
            log = session.get_output()
            session.close()
            raise error.TestFail("Failed to do net-edit: %s\n%s"
                                 % (details, log))

    # Gather test parameters
    net_name = params.get("net_edit_net_name", "editnet")

    virsh_dargs = {'debug': True, 'ignore_status': True}
    virsh_instance = virsh.VirshPersistent(**virsh_dargs)

    # Get all network instance
    nets = network_xml.NetworkXML.new_all_networks_dict(virsh_instance)

    # First check if a bridge of this name already exists
    # Increment suffix integer from 1 then append it to net_name
    # till there is no name conflict.
    if net_name in nets:
        net_name_fmt = net_name + "%d"
        suffix_num = 1
        while ((net_name_fmt % suffix_num) in nets):
            suffix_num += 1

        net_name = net_name_fmt % suffix_num

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
        test_xml.define()
    except xcepts.LibvirtXMLError, detail:
        raise error.TestNAError("Failed to define a test network.\n"
                                "Detail: %s." % detail)

    # Run test case
    try:
        edit_net_xml()

        cmd_result = virsh.net_dumpxml(net_name, debug=True)
        if cmd_result.exit_status:
            raise error.TestFail("Failed to dump xml of virtual network %s" %
                                 net_name)

        # The xml should contain the match_string
        match_string = "100.253"
        xml = cmd_result.stdout.strip()
        if not re.search(match_string, xml):
            raise error.TestFail("The xml is not expected")

    finally:
        test_xml.undefine()
