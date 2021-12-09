import os
import logging

from virttest import virsh

from virttest import libvirt_version

NWFILTER_ETC_DIR = "/etc/libvirt/nwfilter"


def run(test, params, env):
    """
    Test command: virsh nwfilter-list.

    1) Prepare parameters.
    2) Run nwfilter-list command.
    3) Check result.
    """
    # Prepare parameters
    options_ref = params.get("list_options_ref", "")
    status_error = params.get("status_error", "no")
    filter_name = []

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
    cmd_result = virsh.nwfilter_list(options=options_ref, **virsh_dargs)
    output = cmd_result.stdout.strip()
    status = cmd_result.exit_status

    # Check result
    if status_error == "yes":
        if status == 0:
            test.fail("Run successfully with wrong command.")
    elif status_error == "no":
        if status:
            test.fail("Run failed with right command.")

        # Retrieve filter name from output and check the cfg file
        output_list = output.split('\n')
        for i in range(2, len(output_list)):
            filter_name.append(output_list[i].split()[1])
        for i in range(len(filter_name)):
            xml_path = "%s/%s.xml" % (NWFILTER_ETC_DIR, filter_name[i])
            if not os.path.exists(xml_path):
                test.fail("Can't find list filter %s xml under %s"
                          % (filter_name[i], NWFILTER_ETC_DIR))
            else:
                logging.debug("list filter %s xml found under %s" %
                              (filter_name[i], NWFILTER_ETC_DIR))
