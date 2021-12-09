import logging

import aexpect

from virttest import virsh
from virttest import libvirt_xml
from virttest import remote

from virttest import libvirt_version


def run(test, params, env):
    """
    Test virsh nwfilter-edit with uuid.

    1) Prepare parameters.
    2) Run nwfilter-edit command.
    3) Check result.
    4) Clean env
    """
    # Prepare parameters
    filter_name = params.get("edit_filter_name", "")
    status_error = params.get("status_error", "no")
    new_uuid = "11111111-1111-1111-1111-111111111111"
    edit_cmd = ":2s/<uuid>.*$/<uuid>%s<\/uuid>/" % new_uuid

    # Since commit 46a811d, the logic changed for not allow update filter
    # uuid, so decide status_error with libvirt version.
    if libvirt_version.version_compare(1, 2, 7):
        status_error = True
    else:
        status_error = False

    # Backup filter xml
    new_filter = libvirt_xml.NwfilterXML()
    filterxml = new_filter.new_from_filter_dumpxml(filter_name)
    logging.debug("the filter xml is: %s" % filterxml.xmltreefile)

    try:
        # Run command
        session = aexpect.ShellSession("sudo -s")
        try:
            session.sendline("virsh nwfilter-edit %s" % filter_name)
            session.sendline(edit_cmd)
            # Press ESC
            session.send('\x1b')
            # Save and quit
            session.send('ZZ')
            remote.handle_prompts(session, None, None, r"[\#\$]\s*$")
            session.close()
            if not status_error:
                logging.info("Succeed to do nwfilter edit")
            else:
                test.fail("edit uuid should fail but got succeed.")
        except (aexpect.ShellError, aexpect.ExpectError, remote.LoginTimeoutError) as details:
            log = session.get_output()
            session.close()
            if "Try again? [y,n,f,?]:" in log and status_error:
                logging.debug("edit uuid failed as expected.")
            else:
                test.fail("Failed to do nwfilter-edit: %s\n%s"
                          % (details, log))
    finally:
        # Clean env
        virsh.nwfilter_undefine(filter_name, debug=True)
        virsh.nwfilter_define(filterxml.xml, debug=True)
