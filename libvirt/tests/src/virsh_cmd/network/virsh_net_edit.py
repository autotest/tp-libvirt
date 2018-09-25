import logging
import re
import aexpect

from virttest import remote
from virttest import virsh
from virttest import utils_libvirtd
from virttest.libvirt_xml import network_xml
from virttest.libvirt_xml import xcepts


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
        except (aexpect.ShellError, aexpect.ExpectError) as details:
            log = session.get_output()
            session.close()
            test.fail("Failed to do net-edit: %s\n%s"
                      % (details, log))

    # Gather test parameters
    net_name = params.get("net_edit_net_name", "editnet")
    test_create = "yes" == params.get("test_create", "no")

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
        if test_create:
            test_xml.create()
        else:
            test_xml.define()
    except xcepts.LibvirtXMLError as detail:
        test.cancel("Failed to define a test network.\n"
                    "Detail: %s." % detail)

    # Run test case
    try:
        libvirtd = utils_libvirtd.Libvirtd()
        if test_create:
            # Restart libvirtd and check state
            libvirtd.restart()
            net_state = virsh.net_state_dict()
            if (not net_state[net_name]['active'] or
                    net_state[net_name]['autostart'] or
                    net_state[net_name]['persistent']):
                test.fail("Found wrong network states"
                          " after restarting libvirtd: %s"
                          % net_state)
        else:
            libvirtd.restart()
            net_state = virsh.net_state_dict()
            if (net_state[net_name]['active'] or
                    net_state[net_name]['autostart'] or
                    not net_state[net_name]['persistent']):
                test.fail("Found wrong network states: %s" % net_state)
            result = virsh.net_start(net_name)
            logging.debug("start the persistent network return: %s", result)

        edit_net_xml()
        net_state = virsh.net_state_dict()
        # transient Network become persistent after editing
        # and the status should be the same as persistent network
        if (not net_state[net_name]['active'] or
                net_state[net_name]['autostart'] or
                not net_state[net_name]['persistent']):
            test.fail("Found wrong network states"
                      " after editing: %s"
                      % net_state)

        cmd_result = virsh.net_dumpxml(net_name, '--inactive', debug=True)
        if cmd_result.exit_status:
            test.fail("Failed to dump xml of virtual network %s" %
                      net_name)

        # The inactive xml should contain the match_string
        match_string = "100.253"
        xml = cmd_result.stdout.strip()
        if not re.search(match_string, xml):
            test.fail("The xml is not expected")
        # The active xml should not contain the match_string
        if net_state[net_name]['active']:
            cmd_result = virsh.net_dumpxml(net_name, debug=True)
            if cmd_result.exit_status:
                test.fail("Failed to dump active xml of virtual network %s" %
                          net_name)
            match_string = "100.253"
            xml = cmd_result.stdout.strip()
            if re.search(match_string, xml):
                test.fail("The active xml should not change")

    finally:
        test_xml.orbital_nuclear_strike()
