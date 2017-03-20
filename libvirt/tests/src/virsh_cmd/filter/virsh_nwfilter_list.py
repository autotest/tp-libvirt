import os
import logging

from virttest import virsh
from virttest.utils_test import libvirt as utlv

from provider import libvirt_version

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
    status_error = "yes" == params.get("status_error", "no")
    filter_name = []

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
    cmd_result = virsh.nwfilter_list(options=options_ref, **virsh_dargs)
    utlv.check_exit_status(cmd_result, status_error)
    output = cmd_result.stdout.strip()
    if not status_error:
        # Retrieve filter name from output and check the cfg file
        output_list = output.split('\n')
        for line in output_list[2:]:
            filter_name.append(line.split()[1])
        for name in filter_name:
            xml_path = "%s/%s.xml" % (NWFILTER_ETC_DIR, name)
            if not os.path.exists(xml_path):
                test.fail("Can't find list filter %s xml under %s" %
                          (name, NWFILTER_ETC_DIR))
            else:
                logging.debug("list filter %s xml found under %s",
                              name, NWFILTER_ETC_DIR)
