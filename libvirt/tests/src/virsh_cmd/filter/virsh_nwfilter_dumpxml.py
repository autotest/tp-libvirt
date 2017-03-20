import logging

from virttest import virsh
from virttest import libvirt_xml
from virttest.utils_test import libvirt as utlv

from provider import libvirt_version


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
    for line in output[2:]:
        if line.split() == [uuid, name]:
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
    status_error = "yes" == params.get("status_error", "no")

    # acl polkit params
    uri = params.get("virsh_uri")
    unprivileged_user = params.get('unprivileged_user')
    if unprivileged_user:
        if unprivileged_user.count('EXAMPLE'):
            unprivileged_user = 'testacl'

    if not libvirt_version.version_compare(1, 1, 1):
        if params.get('setup_libvirt_polkit') == 'yes':
            test.skip("API acl test not supported in current libvirt version.")

    virsh_dargs = {'ignore_status': True, 'debug': True}
    if params.get('setup_libvirt_polkit') == 'yes':
        virsh_dargs['unprivileged_user'] = unprivileged_user
        virsh_dargs['uri'] = uri

    # Run command
    cmd_result = virsh.nwfilter_dumpxml(filter_name, options=options_ref,
                                        **virsh_dargs)
    output = cmd_result.stdout.strip()
    utlv.check_exit_status(cmd_result, status_error)

    if not status_error:
        new_filter = libvirt_xml.NwfilterXML()
        new_filter['xml'] = output
        uuid = new_filter.uuid
        name = new_filter.filter_name
        if check_list(uuid, name):
            logging.debug("The filter with uuid %s and name %s was found",
                          uuid, name)
        else:
            test.fail("The uuid %s with name %s did not match" % (uuid, name))

        # Run command second time with uuid
        cmd_result = virsh.nwfilter_dumpxml(uuid, options=options_ref,
                                            **virsh_dargs)
        output1 = cmd_result.stdout.strip()
        utlv.check_exit_status(cmd_result, status_error)
        if output1 != output:
            test.fail("nwfilter dumpxml output was different between using "
                      "filter uuid and name")
