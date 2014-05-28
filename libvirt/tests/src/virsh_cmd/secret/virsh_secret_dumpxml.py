import os
import re
import commands
import logging
import tempfile
from autotest.client.shared import error
from virttest import virsh, data_dir

def run(test, params, env):
    """
    Test command: secret-dumpxml <secret>

    Output attributes of a secret as an XML dump to stdout.
    """

    # MAIN TEST CODE ###
    # Process cartesian parameters
    status_error = ("yes" == params.get("status_error", "no"))
    secret_ref = params.get("secret_ref")

    if secret_ref == "secret_valid_uuid":
       # Generate valid uuid
        cmd = "uuidgen"
        status, uuid = commands.getstatusoutput(cmd)
        if status:
            raise error.TestNAError("Failed to generate valid uuid")

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

    fd = open(xmlfile, 'w')
    fd.write(secret_xml)
    fd.close()

    try:
        virsh.secret_define(xmlfile, debug=True)

        cmd_result = virsh.secret_dumpxml(uuid, debug=True)
        output = cmd_result.stdout.strip()
        if not status_error and cmd_result.exit_status:
            raise error.TestFail("Dumping the xml of secret object failed")

        match_string = "<uuid>%s</uuid>" % uuid
        if not re.search(match_string, output):
            raise error.TestFail("The secret xml is not valid")
    finally:
        #Cleanup
        virsh.secret_undefine(uuid, debug=True)

        if os.path.exists(xmlfile):
            os.remove(xmlfile)
