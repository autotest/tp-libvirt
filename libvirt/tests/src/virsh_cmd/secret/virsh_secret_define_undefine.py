import os
import tempfile
import commands
import logging
from autotest.client import utils
from autotest.client.shared import error
from virttest import virsh, data_dir
from virttest.libvirt_xml.secret_xml import SecretXML
from provider import libvirt_version


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

    # libvirt acl related params
    uri = params.get("virsh_uri")
    unprivileged_user = params.get('unprivileged_user')
    define_acl = "yes" == params.get("define_acl", "no")
    undefine_acl = "yes" == params.get("undefine_acl", "no")
    get_value_acl = "yes" == params.get("get_value_acl", "no")
    define_error = "yes" == params.get("define_error", "no")
    undefine_error = "yes" == params.get("undefine_error", "no")
    get_value_error = "yes" == params.get("get_value_error", "no")

    if unprivileged_user:
        if unprivileged_user.count('EXAMPLE'):
            unprivileged_user = 'testacl'

    if not libvirt_version.version_compare(1, 1, 1):
        if params.get('setup_libvirt_polkit') == 'yes':
            raise error.TestNAError("API acl test not supported in current"
                                    + " libvirt version.")

    acl_dargs = {'uri': uri, 'unprivileged_user': unprivileged_user,
                 'debug': True}

    # Get a full path of tmpfile, the tmpfile need not exist
    tmp_dir = data_dir.get_tmp_dir()
    volume_path = os.path.join(tmp_dir, "secret_volume")

    secret_xml_obj = SecretXML(ephemeral, private)
    secret_xml_obj.uuid = uuid
    secret_xml_obj.volume = volume_path
    secret_xml_obj.usage = "volume"

    secret_obj_xmlfile = os.path.join(SECRET_DIR, uuid + ".xml")

    def check_exit_status(result, expect_error=False):
        """
        Check the exit status of virsh commands.

        :param result: Virsh command result object
        :param expect_error: Boolean value, expect command success or fail
        """
        if expect_error:
            if result.exit_status == 0:
                raise error.TestFail("Expect fail, but run successfully.")
            else:
                logging.debug("Command failed as expected.")
        else:
            if result.exit_status != 0:
                raise error.TestFail(result.stderr)

    # Run the test
    try:
        if define_acl:
            utils.run("chmod 666 %s" % secret_xml_obj.xml)
            cmd_result = virsh.secret_define(secret_xml_obj.xml, **acl_dargs)
        else:
            cmd_result = virsh.secret_define(secret_xml_obj.xml, debug=True)
        check_exit_status(cmd_result, define_error)
        if cmd_result.exit_status:
            return

        # Check ephemeral attribute
        exist = os.path.exists(secret_obj_xmlfile)
        if (ephemeral == "yes" and exist) or \
           (ephemeral == "no" and not exist):
            raise error.TestFail("The ephemeral attribute worked not expected")

        # Check private attrbute
        virsh.secret_set_value(uuid, SECRET_BASE64, debug=True)
        if get_value_acl:
            cmd_result = virsh.secret_get_value(uuid, **acl_dargs)
        else:
            cmd_result = virsh.secret_get_value(uuid, debug=True)
        check_exit_status(cmd_result, get_value_error)
        status = cmd_result.exit_status
        err_msg = "The private attribute worked not expected"
        if private == "yes" and not status:
            raise error.TestFail(err_msg)
        if private == "no" and status:
            if not get_value_error:
                raise error.TestFail(err_msg)

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

        if undefine_acl:
            cmd_result = virsh.secret_undefine(uuid, **acl_dargs)
        else:
            cmd_result = virsh.secret_undefine(uuid, debug=True)
            check_exit_status(cmd_result, undefine_error)
    finally:
        # cleanup
        virsh.secret_undefine(uuid, ignore_status=True)
        if os.path.exists(volume_path):
            os.unlink(volume_path)
        if os.path.exists(secret_obj_xmlfile):
            os.unlink(secret_obj_xmlfile)
