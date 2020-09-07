import logging

from virttest import virsh
from virttest import libvirt_xml

from virttest import libvirt_version


def check_list(filter_ref):
    """
    Return True if filter found in nwfilter-list

    :param filter_ref: filter name or uuid
    :return: True if found, False if not found
    """
    filter_list = []
    cmd_result = virsh.nwfilter_list(options="",
                                     ignore_status=True, debug=True)
    output = cmd_result.stdout.strip().split('\n')
    for i in range(2, len(output)):
        filter_list.append(output[i].split())
    for i in range(len(filter_list)):
        if filter_ref in filter_list[i]:
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
    status_error = params.get("status_error", "no")

    # libvirt acl polkit related params
    uri = params.get("virsh_uri")
    unprivileged_user = params.get('unprivileged_user')
    try:
        if unprivileged_user:
            if unprivileged_user.count('EXAMPLE'):
                unprivileged_user = 'testacl'

        if not libvirt_version.version_compare(1, 1, 1):
            if params.get('setup_libvirt_polkit') == 'yes':
                test.cancel("API acl test not supported in current"
                            " libvirt version.")
        # Backup filter xml
        if filter_ref:
            new_filter = libvirt_xml.NwfilterXML()
            filterxml = new_filter.new_from_filter_dumpxml(filter_ref)
            logging.debug("the filter xml is: %s" % filterxml.xmltreefile)
            filter_xml = filterxml.xmltreefile.name

        # Run command
        cmd_result = virsh.nwfilter_undefine(filter_ref, options=options_ref,
                                             unprivileged_user=unprivileged_user,
                                             uri=uri,
                                             ignore_status=True, debug=True)
        status = cmd_result.exit_status

        # Check result
        if status_error == "yes":
            if status == 0:
                test.fail("Run successfully with wrong command.")
        elif status_error == "no":
            if status:
                test.fail("Run failed with right command.")
            chk_result = check_list(filter_ref)
            if chk_result:
                test.fail("filter %s show up in filter list." %
                          filter_ref)
    finally:
        # Clean env
        if status == 0:
            virsh.nwfilter_define(filter_xml, options="",
                                  ignore_status=True, debug=True)
