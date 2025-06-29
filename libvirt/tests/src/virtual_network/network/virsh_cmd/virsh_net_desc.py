# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Nannan Li<nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import aexpect
import re

from virttest import libvirt_version
from virttest import remote
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_network
from virttest.libvirt_xml.network_xml import NetworkXML

VIRSH_ARGS = {'ignore_status': False, 'debug': True}


def virsh_net_desc(test, params, cmd):
    """
    Edit description or title with virsh net-desc by cmdline or
    edit space according to scenario.

    :params: test: test object.
    :params: params: cfg parameter dict.
    :params: cmd: virsh desc updating cmd, delete all content if cmd is empty.
    """
    update_method = params.get("update_method")
    update_options = params.get("opt")
    net_name = params.get("net_name")
    error_msg = params.get("error_msg")

    if update_method == "cmdline":
        result = virsh.net_desc(net_name, extra=cmd+update_options, debug=True)
        libvirt.check_exit_status(result, error_msg)

    elif update_method == "edit_space":
        send_cmd = r"virsh net-desc %s %s --edit %s" % (net_name, cmd, update_options)
        test.log.debug("Send cmd: %s", send_cmd)
        session = aexpect.ShellSession("sudo -s")
        try:
            session.sendline(send_cmd)
            session.send('\x1b')
            session.send('ZZ')
            remote.handle_prompts(session, None, None, r"[\#\$]\s*$")
        except Exception as e:
            if not error_msg:
                test.fail("Error occurred: %s when virsh net-desc --edit" % str(e))
        session.close()


def check_net_desc_result(test, params, get_cmd, expected_string, existed=True):
    """
    Get and check desc or title  by virsh net-desc cmd.

    :params: test, test object.
    :params: params, params object.
    :params: get_cmd, the get cmd for virsh net-desc.
    :params: expected_string, expected result string.
    :params: existed, the flag of expected result string exist or not.
    """
    net_name = params.get("net_name")
    update_options = params.get("opt")

    result = virsh.net_desc(net_name, extra=get_cmd+update_options, debug=True)
    if existed:
        if result.stdout.strip() != expected_string:
            test.fail('Expect "%s" was existed.' % expected_string)
    else:
        if result.stderr.strip() != expected_string:
            test.fail('Expect "%s" was not existed.' % expected_string)
    test.log.debug("Check '%s' PASS in virsh net-desc", expected_string)


def _confirm_existed(params, dumpxml_opt, check_remove):
    """
    Get existed value according to the scenario.

    :params: params, params object.
    :params: dumpxml_opt, virsh net-dumpxml option, '--inactive' or ''
    :params: check_remove, check net-dumpxml after removing.
    :return: expected xml was existed flag.
    """
    opt = params.get('opt')
    network_states = params.get('network_states')

    existed = True
    if ((dumpxml_opt == " " and opt == ' --config' and network_states == 'active_net')
            or (dumpxml_opt == " --inactive" and opt == " --live")
            or (dumpxml_opt == " --inactive" and network_states == 'active_net' and opt in [' --current', ' '])
            or check_remove):
        existed = False

    return existed


def check_net_dumpxml(test, params, expected_xml, check_remove=False):
    """
    Check if expected xml in virsh net-dumpxml.

    :params: test, test object.
    :params: params, params object.
    :params: expected_xml, expected xml string.
    :params: check_remove, check net-dumpxml after removing, default False
    """
    net_name = params.get("net_name")

    dumpxml_opt = [" ",  " --inactive"]
    for dump in dumpxml_opt:
        existed = _confirm_existed(params, dump, check_remove)
        result = virsh.net_dumpxml(net_name, dump, debug=True).stdout.strip()
        if existed:
            if not re.findall(expected_xml, result):
                test.fail('Expect %s was existed' % expected_xml)
        else:
            if re.findall(expected_xml, result):
                test.fail('Expect %s was not existed' % expected_xml)
    test.log.debug("Checked '%s' PASS in active and inactive xml" % expected_xml)


def run(test, params, env):
    """
    Test 'virsh net-desc' with different options to show or modify network description
     or title.
    """
    def setup_test():
        """
        Prepare network status.
        """
        test.log.info("TEST_SETUP: Prepare network status.")
        libvirt_network.ensure_default_network()
        if network_states == "inactive_net":
            virsh.net_destroy(net_name, **VIRSH_ARGS)

    def run_test():
        """
        1. Update and remove network description or title.
        2. Check description xml is existed or removed.
        """
        test.log.info("TEST_STEP1：Set a %s xml for network." % update_item)
        virsh_net_desc(test, params, execute_cmd)
        if error_msg:
            return

        test.log.info("TEST_STEP2: Check correct %s xml in network." % update_item)
        check_net_desc_result(test, params, get_cmd, expected_str)
        check_net_dumpxml(test, params, expected_xml)

        test.log.info("TEST_STEP3-4: Modify and check %s xml in network." % update_item)
        virsh_net_desc(test, params, execute_update_cmd)
        check_net_desc_result(test, params, get_cmd, expected_update_str)
        check_net_dumpxml(test, params, expected_update_xml)

        test.log.info("TEST_STEP5-6: Remove and check %s xml in network." % update_item)
        virsh_net_desc(test, params, remove_opt)
        check_net_desc_result(test, params, get_cmd, removed_msg)
        check_net_dumpxml(test, params, expected_update_xml, check_remove=True)

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        bk_net.sync()
        bkxml.sync()
        libvirt_network.ensure_default_network()

    libvirt_version.is_libvirt_feature_supported(params)
    vm_name = params.get('main_vm')
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    default_net = NetworkXML.new_from_net_dumpxml('default')
    bk_net = default_net.copy()

    net_name = params.get("net_name")
    network_states = params.get("network_states")
    update_item = params.get("update_item")
    error_msg = params.get("error_msg")
    remove_opt = params.get("remove_opt")
    removed_msg = params.get("removed_msg")
    get_cmd = params.get("get_cmd")
    execute_cmd, execute_update_cmd = params.get('execute_cmd'), params.get("execute_update_cmd")
    expected_str, expected_update_str = params.get("expected_str"), params.get("expected_update_str")
    expected_xml, expected_update_xml = params.get("expected_xml"), params.get("expected_update_xml")

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
