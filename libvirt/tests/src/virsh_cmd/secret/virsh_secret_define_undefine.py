import os
import tempfile
import commands
from autotest.client.shared import error
from virttest import virsh, data_dir
from virttest.libvirt_xml.secret_xml import SecretXML

SECRET_DIR = "/etc/libvirt/secrets/"
SECRET_BASE64 = "c2VjcmV0X3Rlc3QK"


def run(test, params, env):
    """
    Test command: virsh secret-define <file>
                  secret-undefine <secret>
    The testcase is to define or modify a secret
    from an XML file, then undefine it
    """

    # MAIN TEST CODE ###
    # Process cartesian parameters
    status_error = ("yes" == params.get("status_error", "no"))
    secret_ref = params.get("secret_ref")
    ephemeral = params.get("ephemeral_value", "no")
    private = params.get("private_value", "no")
    modify_volume = ("yes" == params.get("secret_modify_volume", "no"))
    remove_uuid = ("yes" == params.get("secret_remove_uuid", "no"))

    if secret_ref == "secret_valid_uuid":
        # Generate valid uuid
        cmd = "uuidgen"
        status, uuid = commands.getstatusoutput(cmd)
        if status:
            raise error.TestNAError("Failed to generate valid uuid")

    elif secret_ref == "secret_invalid_uuid":
        uuid = params.get(secret_ref)

    # Get a full path of tmpfile, the tmpfile need not exist
    tmp_dir = data_dir.get_tmp_dir()
    volume_path = os.path.join(tmp_dir, "secret_volume")

    secret_xml_obj = SecretXML(ephemeral, private)
    secret_xml_obj.uuid = uuid
    secret_xml_obj.volume = volume_path
    secret_xml_obj.usage = "volume"

    # Run the test
    try:
        cmd_result = virsh.secret_define(secret_xml_obj.xml, debug=True)
        secret_define_status = cmd_result.exit_status

        # Check status_error
        if status_error and secret_define_status == 0:
            raise error.TestFail("Run successfully with wrong command!")
        elif not status_error and secret_define_status != 0:
            raise error.TestFail("Run failed with right command")

        if secret_define_status != 0:
            return

        # Check ephemeral attribute
        secret_obj_xmlfile = os.path.join(SECRET_DIR, uuid + ".xml")
        exist = os.path.exists(secret_obj_xmlfile)
        if (ephemeral == "yes" and exist) or \
           (ephemeral == "no" and not exist):
            raise error.TestFail("The ephemeral attribute worked not expected")

        # Check private attrbute
        virsh.secret_set_value(uuid, SECRET_BASE64, debug=True)
        cmd_result = virsh.secret_get_value(uuid, debug=True)
        status = cmd_result.exit_status
        if (private == "yes" and status == 0) or \
           (private == "no" and status != 0):
            raise error.TestFail("The private attribute worked not expected")

        if modify_volume:
            volume_path = os.path.join(tmp_dir, "secret_volume_modify")
            secret_xml_obj.volume = volume_path
            cmd_result = virsh.secret_define(secret_xml_obj.xml, debug=True)
            if cmd_result.exit_status == 0:
                raise error.TestFail("Expect fail on redefine after modify "
                                     "volume, but success indeed")
        if remove_uuid:
            secret_xml_obj2 = SecretXML(ephemeral, private)
            secret_xml_obj2.volume = volume_path
            secret_xml_obj2.usage = "volume"
            cmd_result = virsh.secret_define(secret_xml_obj2.xml, debug=True)
            if cmd_result.exit_status == 0:
                raise error.TestFail("Expect fail on redefine after remove "
                                     "uuid, but success indeed")

    finally:
        # cleanup
        if secret_define_status == 0:
            cmd_result = virsh.secret_undefine(uuid, debug=True)
            if cmd_result.exit_status != 0:
                raise error.TestFail("Failed to undefine secret object")
