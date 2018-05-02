import os
import re
import base64
import logging
import locale
from tempfile import mktemp

from avocado.utils import process

from virttest import virsh
from virttest.libvirt_xml.secret_xml import SecretXML

_VIRT_SECRETS_PATH = "/etc/libvirt/secrets"


def check_secret(params):
    """
    Check specified secret value with decoded secret from
    _VIRT_SECRETS_PATH/$uuid.base64
    :params: the parameter dictionary
    """
    secret_decoded_string = ""

    uuid = params.get("secret_ref")
    secret_string = params.get("secret_base64_no_encoded")

    base64_file = os.path.join(_VIRT_SECRETS_PATH, "%s.base64" % uuid)

    if os.access(base64_file, os.R_OK):
        with open(base64_file, 'rb') as base64file:
            base64_encoded_string = base64file.read().strip()
        secret_decoded_string = base64.b64decode(base64_encoded_string).decode(locale.getpreferredencoding())
    else:
        logging.error("Did not find base64_file: %s", base64_file)
        return False

    if secret_string and secret_string != secret_decoded_string:
        logging.error("To expect %s value is %s",
                      secret_string, secret_decoded_string)
        return False

    return True


def create_secret_volume(test, params):
    """
    Define a secret of the volume
    :params: the parameter dictionary
    """
    private = params.get("secret_private", "no")
    desc = params.get("secret_desc", "my secret")
    ephemeral = params.get("secret_ephemeral", "no")
    usage_volume = params.get("secret_usage_volume")
    usage_type = params.get("secret_usage", "volume")

    sec_xml = """
<secret ephemeral='%s' private='%s'>
    <description>%s</description>
    <usage type='%s'>
        <volume>%s</volume>
    </usage>
</secret>
""" % (ephemeral, private, desc, usage_type, usage_volume)

    logging.debug("Prepare the secret XML: %s", sec_xml)
    sec_file = mktemp()
    with open(sec_file, 'w') as xml_object:
        xml_object.write(sec_xml)

    result = virsh.secret_define(sec_file)
    status = result.exit_status

    # Remove temprorary file
    os.unlink(sec_file)

    if status:
        test.fail(result.stderr)


def get_secret_value(test, params):
    """
    Get the secret value
    :params: the parameter dictionary
    """
    base64_file = ""

    uuid = params.get("secret_ref")
    options = params.get("get_secret_options")
    status_error = params.get("status_error", "no")

    result = virsh.secret_get_value(uuid, options)
    status = result.exit_status

    # Get secret XML by UUID
    secret_xml_obj = SecretXML()
    secret_xml = secret_xml_obj.get_secret_details_by_uuid(uuid)

    # If secret is private then get secret failure is an expected error
    if secret_xml.get("secret_private", "no") == "yes":
        status_error = "yes"

    if uuid:
        base64_file = os.path.join(_VIRT_SECRETS_PATH, "%s.base64" % uuid)

    # Don't check result if we don't need to.
    if params.get("check_get_status", "yes") == "no":
        return

    # Check status_error
    if status_error == "yes":
        if status:
            logging.info("It's an expected %s", result.stderr)
        else:
            # Only raise error when the /path/to/$uuid.base64 file
            # doesn't exist
            if not os.access(base64_file, os.R_OK):
                test.fail("%d not a expected command "
                          "return value", status)
    elif status_error == "no":
        if status:
            test.fail(result.stderr)
        else:
            # Check secret value
            if base64_file and check_secret(params):
                logging.info(result.stdout.strip())
            else:
                test.fail("The secret value "
                          "mismatch with result")


def set_secret_value(test, params):
    """
    Set the secet value
    :params: the parameter dictionary
    """
    uuid = params.get("secret_ref")
    options = params.get("set_secret_options")
    status_error = params.get("status_error", "no")
    secret_string = params.get("secret_base64_no_encoded")

    # Encode secret string if it exists
    if secret_string:
        encoding = locale.getpreferredencoding()
        secret_string = base64.b64encode(secret_string.encode(encoding)).decode(encoding)

    result = virsh.secret_set_value(uuid, secret_string, options)
    status = result.exit_status

    # Don't check result if we don't need to.
    if params.get("check_set_status", "yes") == "no":
        return

    # Check status_error
    if status_error == "yes":
        if status:
            logging.info("It's an expected %s", result.stderr)
        else:
            test.fail("%d not a expected command "
                      "return value", status)
    elif status_error == "no":
        if status:
            test.fail(result.stderr)
        else:
            # Check secret value
            if check_secret(params):
                logging.info(result.stdout.strip())
            else:
                test.fail("The secret value "
                          "mismatch with result")


def cleanup(test, params):
    """
    Cleanup secret and volume
    :params: the parameter dictionary
    """
    uuid = params.get("secret_uuid")
    usage_volume = params.get("secret_usage_volume")

    os.unlink(usage_volume)

    if uuid:
        result = virsh.secret_undefine(uuid)
        status = result.exit_status
        if status:
            test.fail(result.stderr)


def run(test, params, env):
    """
    Test set/get secret value for a volume

    1) Positive testing
       1.1) set the private or public secret value
       1.2) get the public secret value
    2) Negative testing
       2.1) get private secret
       2.2) get secret without setting secret value
       2.3) get or set secret with invalid options
       2.4) set secret with doesn't exist UUID
    """

    # Run test case
    uuid = ""

    usage_volume = params.get("secret_usage_volume",
                              "/var/lib/libvirt/images/foo-bar.secret")
    set_secret = params.get("set_secret", "yes")
    get_secret = params.get("get_secret", "yes")

    # If storage volume doesn't exist then create it
    if not os.path.isfile(usage_volume):
        process.run("dd if=/dev/zero of=%s bs=1 count=1 seek=1M" % usage_volume, shell=True)

    # Define secret based on storage volume
    create_secret_volume(test, params)

    # Get secret UUID from secret list
    output = virsh.secret_list(ignore_status=False).stdout.strip()
    sec_list = re.findall(r"\n(.+\S+)\ +\S+\ +(.+\S+)", output)
    logging.debug("Secret list is %s", sec_list)
    if sec_list:
        for sec in sec_list:
            if usage_volume in sec[1]:
                uuid = sec[0].lstrip()
        if uuid:
            logging.debug("Secret uuid is %s", uuid)
            params['secret_uuid'] = uuid
        else:
            test.fail('Cannot find secret %s in:\n %s'
                      % (usage_volume, output))
    else:
        test.fail('No secret found in:\n %s' % output)

    # Update parameters dictionary with automatically generated UUID
    if not params.get('secret_ref'):
        params['secret_ref'] = uuid

    # positive and negative testing #########
    try:
        if set_secret == "yes":
            set_secret_value(test, params)
        if get_secret == "yes":
            get_secret_value(test, params)
    finally:
        cleanup(test, params)
