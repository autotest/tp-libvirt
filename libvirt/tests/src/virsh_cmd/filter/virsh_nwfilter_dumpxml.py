import logging

from virttest import virsh
from virttest import libvirt_xml

from virttest import libvirt_version


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
    Test command: virsh nwfilter-dumpxml.

    1) Prepare parameters.
    2) Run dumpxml command.
    3) Check result.
    """
    # Prepare parameters
    filter_name = params.get("dumpxml_filter_name", "")
    options_ref = params.get("dumpxml_options_ref", "")
    status_error = params.get("status_error", "no")

    # acl polkit params
    uri = params.get("virsh_uri")
    unprivileged_user = params.get('unprivileged_user')
    if unprivileged_user:
        if unprivileged_user.count('EXAMPLE'):
            unprivileged_user = 'testacl'

    if not libvirt_version.version_compare(1, 1, 1):
        if params.get('setup_libvirt_polkit') == 'yes':
            test.cancel("API acl test not supported in current"
                        " libvirt version.")

    virsh_dargs = {'ignore_status': True, 'debug': True}
    if params.get('setup_libvirt_polkit') == 'yes':
        virsh_dargs['unprivileged_user'] = unprivileged_user
        virsh_dargs['uri'] = uri

    # Run command
    cmd_result = virsh.nwfilter_dumpxml(filter_name, options=options_ref,
                                        **virsh_dargs)
    output = cmd_result.stdout.strip()
    status = cmd_result.exit_status

    # Check result
    if status_error == "yes":
        if status == 0:
            test.fail("Run successfully with wrong command.")
    elif status_error == "no":
        if status:
            test.fail("Run failed with right command.")
        # Get uuid and name from output xml and compare with nwfilter-list
        # output
        new_filter = libvirt_xml.NwfilterXML()
        new_filter['xml'] = output
        uuid = new_filter.uuid
        name = new_filter.filter_name
        if check_list(uuid, name):
            logging.debug("The filter with uuid %s and name %s" % (uuid, name) +
                          " from nwfilter-dumpxml was found in"
                          " nwfilter-list output")
        else:
            test.fail("The uuid %s with name %s from" % (uuid, name) +
                      " nwfilter-dumpxml did not match with"
                      " nwfilter-list output")

        # Run command second time with uuid
        cmd_result = virsh.nwfilter_dumpxml(uuid, options=options_ref,
                                            **virsh_dargs)
        output1 = cmd_result.stdout.strip()
        status1 = cmd_result.exit_status
        if status_error == "yes":
            if status1 == 0:
                test.fail("Run successfully with wrong command.")
        elif status_error == "no":
            if status1:
                test.fail("Run failed with right command.")
        if output1 != output:
            test.fail("nwfilter dumpxml output was different" +
                      " between using filter uuid and name")
