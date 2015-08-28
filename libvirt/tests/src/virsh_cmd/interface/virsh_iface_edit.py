import os
import re
import logging
from autotest.client import utils
from autotest.client.shared import error
from virttest import aexpect
from virttest import remote
from virttest.utils_test import libvirt
from virttest import utils_net
from virttest import virsh

NETWORK_SCRIPT = "/etc/sysconfig/network-scripts/ifcfg-"


def get_ifstart_mode(iface_name):
    """
    Get the start mode of a interface.
    """
    start_mode = None
    try:
        xml = virsh.iface_dumpxml(iface_name, "--inactive", "", debug=True)
        start_mode = re.findall("start mode='(\S+)'", xml)[0]
    except (error.CmdError, IndexError):
        logging.error("Fail to get start mode for interface %s", iface_name)
    return start_mode


def edit_ifstart_mode(iface_name, old_mode, new_mode):
    """
    Set the start mode of a interface.
    """
    edit_cmd = ":%s/mode='{0}'/mode='{1}'".format(old_mode, new_mode)
    session = aexpect.ShellSession("sudo -s")
    try:
        session.sendline("virsh iface-edit %s" % iface_name)
        logging.info("Change start mode from %s to %s", old_mode, new_mode)
        session.sendline(edit_cmd)
        session.send('\x1b')
        session.send('ZZ')
        remote.handle_prompts(session, None, None, r"[\#\$]\s*$")
        session.close()
    except (aexpect.ShellError, aexpect.ExpectError), details:
        log = session.get_output()
        session.close()
        raise error.TestFail("Failed to do iface-edit: %s\n%s"
                             % (details, log))


def run(test, params, env):
    """
    Test command: virsh iface-edit <interface>

    Edit interface start mode in this case.
    """
    iface_name = params.get("iface_name", "lo")
    status_error = "yes" == params.get("status_error", "no")
    if not libvirt.check_iface(iface_name, "exists", "--all"):
        raise error.TestError("Interface '%s' not exists" % iface_name)
    iface_script = NETWORK_SCRIPT + iface_name
    iface_script_bk = os.path.join(test.tmpdir, "iface-%s.bk" % iface_name)
    net_iface = utils_net.Interface(name=iface_name)
    iface_is_up = net_iface.is_up()
    old_ifstart_mode = get_ifstart_mode(iface_name)
    if not old_ifstart_mode:
        raise error.TestError("Get start mode fail")
    if old_ifstart_mode == "onboot":
        new_ifstart_mode = "none"
    else:
        new_ifstart_mode = "onboot"
    try:
        # Backup interface script
        utils.run("cp %s %s" % (iface_script, iface_script_bk))

        # Edit interface
        edit_ifstart_mode(iface_name, old_ifstart_mode, new_ifstart_mode)

        # Restart interface
        if iface_is_up:
            net_iface.down()
        utils.run("ifup %s" % iface_name)

        after_edit_mode = get_ifstart_mode(iface_name)
        if not after_edit_mode:
            raise error.TestError("Get start mode fail")
        if new_ifstart_mode == after_edit_mode:
            logging.debug("Interface start mode change to %s", new_ifstart_mode)
        else:
            raise error.TestFail("Unexpect interface start mode: %s"
                                 % after_edit_mode)
    finally:
        net_iface.down()
        utils.run("mv %s %s" % (iface_script_bk, iface_script))
        if iface_is_up:
            utils.run("ifup %s" % iface_name)
