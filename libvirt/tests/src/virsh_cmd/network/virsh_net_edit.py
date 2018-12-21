import logging
import re
import aexpect

from virttest import remote
from virttest import virsh
from virttest import utils_libvirtd
from virttest.libvirt_xml import network_xml
from virttest.libvirt_xml import xcepts
from avocado.utils import process


def run(test, params, env):
    """
    Test command: virsh net-edit <network>

    1) Define a temp virtual network
    2) Execute virsh net-edit to modify it
    3) Dump its xml then check it
    """

    def edit_net_xml(edit_cmd, expect_error, **dargs):
        """
        Edit net xml with virsh net-edit

        :params edit_cmd: The edit cmd to execute
        :params expect_error: Boolen, expect success or not
        :params **dargs: The virsh edit's option
        """
        logging.debug("edit_cmd: %s", edit_cmd)
        readonly = dargs.get("readonly", False)
        session = aexpect.ShellSession("sudo -s")
        try:
            logging.info("Execute virsh net-edit %s", net_name)
            virsh_cmd = "virsh net-edit %s" % net_name
            if readonly:
                virsh_cmd = "virsh -r net-edit %s" % net_name
            logging.debug("virsh_cmd: %s", virsh_cmd)
            session.sendline(virsh_cmd)
            session.sendline(edit_cmd)
            session.send('\x1b')
            session.send('ZZ')
            remote.handle_prompts(session, None, None, r"[\#\$]\s*$")
            session.close()
        except (aexpect.ShellError, aexpect.ExpectError, remote.LoginTimeoutError) as details:
            log = session.get_output()
            session.close()
            if not expect_error:
                test.fail("Failed to do net-edit: %s\n%s"
                          % (details, log))
            logging.debug("Expected error: %s" % log)
            if readonly and "read only" not in log:
                test.fail("Not expected error")

    # Gather test parameters
    net_name = params.get("net_edit_net_name", "editnet")
    test_create = "yes" == params.get("test_create", "no")

    virsh_dargs = {'debug': True, 'ignore_status': True}
    virsh_instance = virsh.VirshPersistent(**virsh_dargs)

    change_attribute = params.get("attribute", None)
    old_value = params.get("old_value", None)
    new_value = params.get("new_value", None)
    edit_type = params.get("edit_type", "modify")
    status_error = (params.get("status_error", "no") == "yes")
    readonly = (params.get("net_edit_readonly", "no") == "yes")

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
        if change_attribute == "uuid":
            # if the attribute need to change is uuid, the old uuid should get
            # from current network, and new uuid can generate by uuidgen
            new_value = process.run("uuidgen", shell=True).stdout[:-1]
            old_value = virsh.net_uuid(net_name).stdout.strip()
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

        if edit_type == "modify":
            edit_cmd = r":%%s/%s=\'%s\'/%s=\'%s\'" % (change_attribute, old_value,
                                                      change_attribute, new_value)
            match_string = "%s=\'%s\'" % (change_attribute, new_value)

        if edit_type == "delete":
            match_string = "%s" % change_attribute
            # Pattern to be more accurate
            if old_value and change_attribute != 'uuid':
                match_string = "%s=\'%s\'" % (change_attribute, old_value)
            else:
                match_string = old_value
            edit_cmd = r":/%s/d" % match_string

        edit_net_xml(edit_cmd, status_error, readonly=readonly)

        net_state = virsh.net_state_dict()
        # transient Network become persistent after editing
        if not status_error and (not net_state[net_name]['active'] or
                                 net_state[net_name]['autostart'] or
                                 not net_state[net_name]['persistent']):
            test.fail("Found wrong network states"
                      " after editing: %s"
                      % net_state)

        cmd_result = virsh.net_dumpxml(net_name, '--inactive', debug=True)
        if cmd_result.exit_status:
            test.fail("Failed to dump xml of virtual network %s" %
                      net_name)

        # The inactive xml should contain the match string
        xml = cmd_result.stdout.strip()
        if edit_type == "modify":
            if not status_error and not re.search(match_string, xml):
                test.fail("The inactive xml should contain the change '%s'"
                          % match_string)
            if status_error and re.search(match_string, xml):
                test.fail("Expect to modify failure but run success")

        if edit_type == "delete":
            if not status_error and re.search(match_string, xml):
                test.fail("The inactive xml should delete the change '%s'"
                          % match_string)
            if status_error and not re.search(match_string, xml):
                test.fail("Expect to delete failure but run success")

        # The active xml should not contain the match string
        if net_state[net_name]['active']:
            if not status_error:
                cmd_result = virsh.net_dumpxml(net_name, debug=True)
                if cmd_result.exit_status:
                    test.fail("Failed to dump active xml of virtual network %s" %
                              net_name)
                xml = cmd_result.stdout.strip()
                if edit_type == "modify" and re.search(match_string, xml):
                    test.fail("The active xml should not contain the change '%s'"
                              % match_string)
                if edit_type == "delete" and not re.search(match_string, xml):
                    test.fail("The active xml should not delete the change '%s'"
                              % match_string)
    finally:
        test_xml.orbital_nuclear_strike()
