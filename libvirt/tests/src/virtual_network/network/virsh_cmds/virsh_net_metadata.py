import aexpect
import re

from virttest import libvirt_version
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_network
from virttest.libvirt_xml.network_xml import NetworkXML

VIRSH_ARGS = {'ignore_status': False, 'debug': True}


def edit_metadata(test, params, metadata_cmd, **dargs):
    """
    Edit metadata with cmdline or edit space according to scenario.

    :params: test: test object.
    :params: params: cfg parameter dict.
    :params: metadata_cmd: metadata updating cmd.
    :param dargs: mutable parameter dict.
    """
    update_options = params.get("opt")
    update_method = params.get("update_method")
    metadata_uri = params.get("metadata_uri")
    net_name = params.get("net_name")
    error_msg = params.get("error_msg")

    if update_method == "cmdline":
        result = virsh.net_metadata(net_name, metadata_uri,
                                    extra=metadata_cmd+update_options, debug=True)
        libvirt.check_exit_status(result, error_msg)

    elif update_method == "edit_space":
        prefix = re.findall("(--key.* )-", metadata_cmd)[0]
        ele_name = re.findall("--set \'(.*)\' ", metadata_cmd)[0]

        send_cmd = r"virsh net-metadata %s --uri %s %s --edit %s" % (
            net_name, metadata_uri, prefix, update_options)
        test.log.debug("Send updating metadate cmd: %s", send_cmd)

        session = aexpect.ShellSession("sudo -s")
        try:
            session.sendline(send_cmd)
            if "clean_content" in dargs:
                session.send('dG')
            session.send('i')
            session.sendline(ele_name)
            session.send('\x1b')
            session.send('ZZ')
            session.read_until_any_line_matches(
                [r"Metadata modified"], timeout=10, internal_timeout=1)
        except Exception as e:
            if not error_msg:
                test.fail("Error occurred: %s when virsh net-metadata --edit" % str(e))
        session.close()


def check_net_metadata_result(test, params, expected_string, existed=True):
    """
    Check expected result in virsh net-metadata.

    :params: test, test object.
    :params: params, params object.
    :params: expected_string, expected result string.
    :params: existed, the flag of expected result string exist or not.
    """
    update_options = params.get("opt")
    metadata_uri = params.get("metadata_uri")
    net_name = params.get("net_name")

    result = virsh.net_metadata(net_name, metadata_uri,
                                extra=update_options, debug=True)
    if existed:
        if result.stdout.strip() != expected_string:
            test.fail('Expect "%s" was existed.' % expected_string)
    else:
        # libvirt >= 9.8 return empty stdout/stderr
        if result.stderr.strip():
            if result.stderr.strip() != expected_string:
                test.fail('Expect "%s" was existed.' % expected_string)
        else:
            test.log.debug("No metadata present and no error — new libvirt behaviour.")


def check_net_dumpxml(test, params, expected_xml, exist):
    """
    Check if expected xml in virsh net-dumpxml.

    :params: test, test object.
    :params: params, params object.
    :params: expected_xml, expected xml string.
    :params: exist, exist or not.
    """
    net_name = params.get("net_name")
    dumpxml_opt = params.get("dumpxml_opt")

    result = virsh.net_dumpxml(net_name, dumpxml_opt).stdout.strip()
    if exist:
        if not re.findall(expected_xml, result):
            test.fail('Expect %s was existed but got %s.' % (expected_xml, result))
    else:
        if re.findall(expected_xml, result):
            test.fail('Expect %s was not existed but got %s.' % (expected_xml, result))

    test.log.debug("Checked %s PASS" % expected_xml)


def run(test, params, env):
    """
    Test 'virsh net-metadata' with different options to show or
    modify the XML metadata of a network.
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
        1. Update and remove network metadata.
        2. Check metadata xml is existed or removed.
        """
        test.log.info("TEST_STEP1：Set a xml metadata for network.")
        edit_metadata(test, params, metadata_cmd=metadata_extra)
        if error_msg:
            return

        test.log.info("TEST_STEP2: Check correct metadata xml in network.")
        check_net_metadata_result(test, params, expected_xml)
        check_net_dumpxml(test, params, expected_metadata_xml, exist)

        test.log.info("TEST_STEP3-4: Modify and check metadata xml in network.")
        edit_metadata(test, params, metadata_cmd=metadata_extra_update, clean_content=True)
        check_net_metadata_result(test, params, expected_update_xml)
        check_net_dumpxml(test, params, expected_update_metadata_xml, exist)

        test.log.info("TEST_STEP5-6: Remove and check metadata xml in network.")
        virsh.net_metadata(net_name, metadata_uri,
                           extra=update_options+remove_opt, **VIRSH_ARGS)
        check_net_metadata_result(test, params, removed_msg, existed=False)
        check_net_dumpxml(test, params, expected_update_metadata_xml, exist=False)

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
    metadata_extra = params.get('metadata_extra')
    metadata_extra_update = params.get("metadata_extra_update")
    metadata_uri = params.get("metadata_uri")
    update_options = params.get("opt")
    error_msg = params.get("error_msg")
    remove_opt = params.get("remove_opt")
    expected_xml = params.get("expected_xml")
    expected_metadata_xml = params.get("expected_metadata_xml")
    expected_update_xml = params.get("expected_update_xml")
    expected_update_metadata_xml = params.get("expected_update_metadata_xml")
    exist = params.get("exist", "no") == "yes"
    removed_msg = params.get("removed_msg")

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
