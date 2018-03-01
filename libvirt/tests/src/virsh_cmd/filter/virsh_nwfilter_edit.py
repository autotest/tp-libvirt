import logging
import aexpect

from virttest import virsh
from virttest import libvirt_xml
from virttest import remote
from virttest.utils_test import libvirt as utlv


def edit_filter_xml(filter_name, edit_cmd):
    """
    Edit network filter xml

    :param filter_name: filter name or uuid
    :param edit_cmd: edit command list in interactive mode
    :return: True or False
    """
    session = aexpect.ShellSession("sudo -s")
    try:
        session.sendline("virsh nwfilter-edit %s" % filter_name)
        for i in edit_cmd:
            session.sendline(i)
        # Press ESC
        session.send('\x1b')
        # Save and quit
        session.send('ZZ')
        remote.handle_prompts(session, None, None, r"[\#\$]\s*$")
        session.close()
        logging.info("Succeed to do nwfilter edit")
        return True
    except (aexpect.ShellError, aexpect.ExpectError), details:
        log = session.get_output()
        session.close()
        logging.error("Failed to do nwfilter-edit: %s\n%s", details, log)
        return False


def run(test, params, env):
    """
    Test command: virsh nwfilter-edit.

    1) Prepare parameters.
    2) Run nwfilter-edit command.
    3) Check result.
    4) Clean env
    """
    # Prepare parameters
    filter_name = params.get("edit_filter_name", "")
    filter_ref = params.get("edit_filter_ref", "")
    edit_priority = params.get("edit_priority", "")
    extra_option = params.get("edit_extra_option", "")
    status_error = "yes" == params.get("status_error", "no")
    edit_cmd = []
    edit_cmd.append(":1s/priority=.*$/priority='" + edit_priority + "'>")

    if status_error:
        edit_result = virsh.nwfilter_edit(filter_name, extra_option,
                                          ignore_status=True, debug=True)
        utlv.check_exit_status(edit_result, status_error)
    else:
        # Backup filter xml
        new_filter = libvirt_xml.NwfilterXML()
        filterxml = new_filter.new_from_filter_dumpxml(filter_name)
        logging.debug("the filter xml is: %s", filterxml.xmltreefile)
        filter_xml = filterxml.xmltreefile.name
        uuid = filterxml.uuid
        pre_priority = filterxml.filter_priority

        # Run command
        if filter_ref == "name":
            ret = edit_filter_xml(filter_name, edit_cmd)
        elif filter_ref == "uuid":
            ret = edit_filter_xml(uuid, edit_cmd)
        if not ret:
            test.fail("Fail to do nwfiter-edit with cmd: %s" % edit_cmd)

        # check and clean env
        post_filter = libvirt_xml.NwfilterXML()
        postxml = post_filter.new_from_filter_dumpxml(filter_name)
        logging.debug("the filter xml after edit is: %s",
                      postxml.xmltreefile)
        post_priority = postxml.filter_priority
        if ((pre_priority == edit_priority and
             post_priority != pre_priority) or
                (pre_priority != edit_priority and
                 post_priority == pre_priority)):
            test.fail("nwfilter-edit fail to update filter priority")

        # Clean env
        if post_priority != pre_priority:
            virsh.nwfilter_define(filter_xml, options="",
                                  ignore_status=True, debug=True)
