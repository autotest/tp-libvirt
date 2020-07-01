import os
import logging

from virttest import virsh
from virttest import xml_utils
from virttest import libvirt_xml
from virttest.utils_test import libvirt as utlv

from virttest import libvirt_version

NWFILTER_ETC_DIR = "/etc/libvirt/nwfilter"


def check_list(uuid, name):
    """
    Return True if filter found in nwfilter-list

    :param uuid: filter uuid
    :param name: filter name
    :return: True if found, False if not found
    """
    cmd_result = virsh.nwfilter_list(options="",
                                     ignore_status=True, debug=True)
    output = cmd_result.stdout.strip().split('\n')
    for i in range(2, len(output)):
        if output[i].split() == [uuid, name]:
            return True
    return False


def run(test, params, env):
    """
    Test command: virsh nwfilter-define.

    1) Prepare parameters.
    2) Set options of virsh define.
    3) Run define command.
    4) Check result.
    5) Clean env
    """
    # Prepare parameters
    filter_name = params.get("filter_name", "testcase")
    filter_uuid = params.get("filter_uuid",
                             "11111111-b071-6127-b4ec-111111111111")
    exist_filter = params.get("exist_filter", "no-mac-spoofing")
    filter_xml = params.get("filter_create_xml_file")
    options_ref = params.get("options_ref", "")
    status_error = params.get("status_error", "no")
    boundary_test_skip = "yes" == params.get("boundary_test_skip")
    new_uuid = "yes" == params.get("new_uuid", 'no')
    bug_url = params.get("bug_url")

    # libvirt acl polkit related params
    uri = params.get("virsh_uri")
    unprivileged_user = params.get('unprivileged_user')
    if unprivileged_user:
        if unprivileged_user.count('EXAMPLE'):
            unprivileged_user = 'testacl'

    if not libvirt_version.version_compare(1, 1, 1):
        if params.get('setup_libvirt_polkit') == 'yes':
            test.cancel("API acl test not supported in current"
                        " libvirt version.")

    if exist_filter == filter_name and new_uuid:
        # Since commit 46a811d, update filter with new uuid will fail.
        if libvirt_version.version_compare(1, 2, 7):
            status_error = 'yes'
        else:
            status_error = 'no'

    try:
        if filter_xml == "invalid-filter-xml":
            tmp_xml = xml_utils.TempXMLFile()
            tmp_xml.write('"<filter><<<BAD>>><\'XML</name\>'
                          '!@#$%^&*)>(}>}{CORRUPTE|>!</filter>')
            tmp_xml.flush()
            filter_xml = tmp_xml.name
            logging.info("Test invalid xml is: %s" % filter_xml)
        elif filter_xml:
            # Create filter xml
            new_filter = libvirt_xml.NwfilterXML()
            filterxml_backup = new_filter.new_from_filter_dumpxml(exist_filter)
            # Backup xml if only update exist filter
            if exist_filter == filter_name and not new_uuid:
                filter_uuid = filterxml_backup.uuid
                params['filter_uuid'] = filter_uuid

            filterxml = utlv.create_nwfilter_xml(params)
            filterxml.xmltreefile.write(filter_xml)

        # Run command
        cmd_result = virsh.nwfilter_define(filter_xml, options=options_ref,
                                           unprivileged_user=unprivileged_user,
                                           uri=uri,
                                           ignore_status=True, debug=True)
        status = cmd_result.exit_status

        # Check result
        chk_result = check_list(filter_uuid, filter_name)
        xml_path = "%s/%s.xml" % (NWFILTER_ETC_DIR, filter_name)
        if status_error == "yes":
            if status == 0:
                if boundary_test_skip:
                    test.cancel("Boundary check commit 4f20943 not"
                                " in this libvirt build yet.")
                else:
                    err_msg = "Run successfully with wrong command."
                    if bug_url:
                        err_msg += " Check more info in %s" % bug_url
                    test.fail(err_msg)
        elif status_error == "no":
            if status:
                err_msg = "Run failed with right command."
                if bug_url:
                    err_msg += " Check more info in %s" % bug_url
                test.fail(err_msg)
            if not chk_result:
                test.fail("Can't find filter in nwfilter-list" +
                          " output")
            if not os.path.exists(xml_path):
                test.fail("Can't find filter xml under %s" %
                          NWFILTER_ETC_DIR)
            logging.info("Dump the xml after define:")
            virsh.nwfilter_dumpxml(filter_name,
                                   ignore_status=True,
                                   debug=True)

    finally:
        # Clean env
        if exist_filter == filter_name:
            logging.info("Restore exist filter: %s" % exist_filter)
            virsh.nwfilter_undefine(filter_name, ignore_status=True)
            virsh.nwfilter_define(filterxml_backup.xml, ignore_status=True)
        else:
            if chk_result:
                virsh.nwfilter_undefine(filter_name, ignore_status=True)
        if os.path.exists(filter_xml):
            os.remove(filter_xml)
