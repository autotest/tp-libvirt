import os
import re
import commands
import logging
import tempfile
from autotest.client.shared import error
from virttest import virsh
from virttest import data_dir
from virttest.libvirt_xml.secret_xml import SecretXML


def run(test, params, env):
    """
    Test command: virsh secret-list

    Returns a list of secrets
    """

    # MAIN TEST CODE ###
    # Process cartesian parameters
    status_error = ("yes" == params.get("status_error", "no"))
    secret_list_option = params.get("secret_list_option", "")

    num = 0
    uuid_list = []
    for i in ['yes', 'no']:
        for j in ['yes', 'no']:
            # Generate valid uuid
            cmd = "uuidgen"
            status, uuid = commands.getstatusoutput(cmd)
            if status:
                raise error.TestNAError("Failed to generate valid uuid")
            uuid_list.append(uuid)

            # Get a full path of tmpfile, the tmpfile need not exist
            tmp_dir = data_dir.get_tmp_dir()
            volume_path = os.path.join(tmp_dir, "secret_volume_%s_%s" % (i, j))

            secret_xml_obj = SecretXML(ephemeral=i, private=j)
            secret_xml_obj.uuid = uuid
            secret_xml_obj.volume = volume_path
            secret_xml_obj.usage = "volume"
            secret_xml_obj.description = "test"

            virsh.secret_define(secret_xml_obj.xml, debug=True)

    try:
        cmd_result = virsh.secret_list(secret_list_option, debug=True)
        output = cmd_result.stdout.strip()
        exit_status = cmd_result.exit_status
        if not status_error and exit_status != 0:
            raise error.TestFail("Run failed with right command")
        if status_error and exit_status == 0:
            raise error.TestFail("Run successfully with wrong command!")

        # Reture if secret-list failed
        if exit_status != 0:
            return

        # Check the result
        m1 = re.search(uuid_list[0], output)
        m2 = re.search(uuid_list[1], output)
        m3 = re.search(uuid_list[2], output)
        m4 = re.search(uuid_list[3], output)

        if secret_list_option.find("--no-ephemeral") >= 0:
            if m1 or m2:
                raise error.TestFail("Secret object %s, %s shouldn't be listed"
                                     " out" % (uuid_list[0], uuid_list[1]))
            if secret_list_option.find("--private") >= 0:
                if not m3:
                    raise error.TestFail("Failed list secret object %s" %
                                         uuid_list[2])
                if m4:
                    raise error.TestFail("Secret object %s shouldn't be listed"
                                         " out" % uuid_list[3])
            elif secret_list_option.find("--no-private") >= 0:
                if not m4:
                    raise error.TestFail("Failed list secret object %s" %
                                         uuid_list[3])
                if m3:
                    raise error.TestFail("Secret object %s shouldn't be listed"
                                         " out" % uuid_list[2])
            else:
                if not m3 or not m4:
                    raise error.TestFail("Failed list secret object %s, %s" %
                                         (uuid_list[2], uuid_list[3]))
        elif secret_list_option.find("--ephemeral") >= 0:
            if m3 or m4:
                raise error.TestFail("Secret object %s, %s shouldn't be listed"
                                     " out" % (uuid_list[2], uuid_list[3]))
            if secret_list_option.find("--private") >= 0:
                if not m1:
                    raise error.TestFail("Failed list secret object %s" %
                                         uuid_list[0])
                if m2:
                    raise error.TestFail("Secret object %s shouldn't be listed"
                                         " out" % uuid_list[1])
            elif secret_list_option.find("--no-private") >= 0:
                if not m2:
                    raise error.TestFail("Failed list secret object %s" %
                                         uuid_list[1])
                if m1:
                    raise error.TestFail("Secret object %s shouldn't be listed"
                                         " out" % uuid_list[0])
            else:
                if not m1 or not m2:
                    raise error.TestFail("Failed list secret object %s, %s" %
                                         (uuid_list[0], uuid_list[1]))
        elif secret_list_option.find("--private") >= 0:
            if not m1 or not m3:
                raise error.TestFail("Failed list secret object %s, %s" %
                                     (uuid_list[0], uuid_list[2]))
            if m2 or m4:
                raise error.TestFail("Secret object %s and %s should't be "
                                     "listed out"
                                     % (uuid_list[1], uuid_list[3]))
        elif secret_list_option.find("--no-private") >= 0:
            if not m2 or not m4:
                raise error.TestFail("Failed list secret object %s, %s" %
                                     (uuid_list[1], uuid_list[3]))
            if m1 or m3:
                raise error.TestFail("Secret object %s and %s shouldn't be "
                                     "listed out" %
                                     (uuid_list[0], uuid_list[2]))
        elif secret_list_option is None:
            if not m1 or not m2 or not m3 or not m4:
                raise error.TestFail("Fail to list all secret objects: %s" %
                                     uuid_list)

    finally:
        #Cleanup
        for i in range(0, 4):
            virsh.secret_undefine(uuid_list[i], debug=True)
