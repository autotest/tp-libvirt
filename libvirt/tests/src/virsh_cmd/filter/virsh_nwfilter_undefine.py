import logging

from virttest import virsh
from virttest import libvirt_xml
from virttest.utils_test import libvirt as utlv

from provider import libvirt_version


def check_list(filter_ref):
    """
    Return True if filter found in nwfilter-list

    :param filter_ref: filter name or uuid
    :return: True if found, False if not found
    """
    cmd_result = virsh.nwfilter_list(options="",
                                     ignore_status=True, debug=True)
    output = cmd_result.stdout.strip()
    if filter_ref in output:
        return True

    return False


def run(test, params, env):
    """
    Test command: virsh nwfilter-undefine.

    1) Prepare parameters.
    2) Run nwfilter-undefine command.
    3) Check result.
    4) Clean env
    """
    # Prepare parameters
    filter_ref = params.get("undefine_filter_ref", "")
    options_ref = params.get("undefine_options_ref", "")
    status_error = "yes" == params.get("status_error", "no")

    # libvirt acl polkit related params
    uri = params.get("virsh_uri")
    unprivileged_user = params.get('unprivileged_user')
    if unprivileged_user:
        if unprivileged_user.count('EXAMPLE'):
            unprivileged_user = 'testacl'

    if not libvirt_version.version_compare(1, 1, 1):
        if params.get('setup_libvirt_polkit') == 'yes':
            test.skip("API acl test not supported in current libvirt version.")
    # Backup filter xml
    if filter_ref:
        new_filter = libvirt_xml.NwfilterXML()
        filterxml = new_filter.new_from_filter_dumpxml(filter_ref)
        logging.debug("the filter xml is: %s", filterxml.xmltreefile)
        filter_xml = filterxml.xmltreefile.name

    status = False
    try:
        # Run command
        cmd_result = virsh.nwfilter_undefine(filter_ref, options=options_ref,
                                             unprivileged_user=unprivileged_user,
                                             uri=uri,
                                             ignore_status=True, debug=True)
        status = bool(cmd_result.exit_status)
        utlv.check_exit_status(cmd_result, status_error)
        if not status_error and check_list(filter_ref):
            status = True
            test.fail("filter %s show up in filter list." % filter_ref)
    finally:
        # Clean env
        if not status:
            virsh.nwfilter_define(filter_xml, options="",
                                  ignore_status=True, debug=True)
