import os
import re
import tempfile

from avocado.utils import process

from virttest import virsh
from virttest import data_dir

from virttest import libvirt_version


def run(test, params, env):
    """
    Test command: secret-dumpxml <secret>

    Output attributes of a secret as an XML dump to stdout.
    """

    # MAIN TEST CODE ###
    # Process cartesian parameters
    status_error = ("yes" == params.get("status_error", "no"))
    secret_ref = params.get("secret_ref")

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

    virsh_dargs = {'debug': True}
    if params.get('setup_libvirt_polkit') == 'yes':
        virsh_dargs['unprivileged_user'] = unprivileged_user
        virsh_dargs['uri'] = uri

    if secret_ref == "secret_valid_uuid":
        # Generate valid uuid
        cmd = "uuidgen"
        status, uuid = process.getstatusoutput(cmd)
        if status:
            test.cancel("Failed to generate valid uuid")

    # Get a full path of tmpfile, the tmpfile need not exist
    tmp_dir = data_dir.get_tmp_dir()
    volume_path = os.path.join(tmp_dir, "secret_volume")

    secret_xml = """
<secret ephemeral='no' private='yes'>
  <uuid>%s</uuid>
  <usage type='volume'>
    <volume>%s</volume>
  </usage>
</secret>
""" % (uuid, volume_path)

    # Write secret xml into a tmpfile
    tmp_file = tempfile.NamedTemporaryFile(prefix=("secret_xml_"),
                                           dir=tmp_dir)
    xmlfile = tmp_file.name
    tmp_file.close()

    with open(xmlfile, 'w') as fd:
        fd.write(secret_xml)

    try:
        virsh.secret_define(xmlfile, debug=True)

        cmd_result = virsh.secret_dumpxml(uuid, **virsh_dargs)
        output = cmd_result.stdout.strip()
        if not status_error and cmd_result.exit_status:
            test.fail("Dumping the xml of secret object failed")

        match_string = "<uuid>%s</uuid>" % uuid
        if not re.search(match_string, output):
            test.fail("The secret xml is not valid")
    finally:
        #Cleanup
        virsh.secret_undefine(uuid, debug=True)

        if os.path.exists(xmlfile):
            os.remove(xmlfile)
