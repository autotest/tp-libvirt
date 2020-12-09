import os
import re
import base64
import logging
import locale
import time
from tempfile import mktemp

from avocado.utils import process

from virttest import data_dir
from virttest import virsh
from virttest import libvirt_version
from virttest import virt_vm

from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml
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
    secret_encode = "yes" == params.get("secret_string_base64_encode", "yes")

    base64_file = os.path.join(_VIRT_SECRETS_PATH, "%s.base64" % uuid)

    if secret_encode:
        if os.access(base64_file, os.R_OK):
            with open(base64_file, 'rb') as base64file:
                base64_encoded_string = base64file.read().strip()
            secret_decoded_string = base64.b64decode(base64_encoded_string)\
                .decode(locale.getpreferredencoding())
        else:
            logging.error("Did not find base64_file: %s", base64_file)
            return False
    else:
        secret_decoded_string = secret_string
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

    result = virsh.secret_get_value(uuid, options, debug=True)
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
    secret_encode = "yes" == params.get("secret_string_base64_encode", "yes")
    secret_file = "yes" == params.get("secret_file", "no")

    if options and "interactive" in options:
        cmd = "secret-set-value %s --%s" % (uuid, options)
        virsh_session = virsh.VirshSession(virsh_exec=virsh.VIRSH_EXEC,
                                           auto_close=True)
        virsh_session.sendline(cmd)
        # Wait for 5s
        time.sleep(5)
        virsh_session.sendline(secret_string)
        # Wait for 5s to get stripped output
        time.sleep(5)
        output = virsh_session.get_stripped_output()
        exit_status = 0 if "Secret value set" in output else 1
        result = process.CmdResult(cmd, output, output, exit_status)
    else:
        result = virsh.secret_set_value(uuid, secret_string, options=options,
                                        encode=secret_encode,
                                        use_file=secret_file,
                                        debug=True)
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
    vm_name = params.get("main_vm", "")

    os.unlink(usage_volume)

    if uuid:
        result = virsh.secret_undefine(uuid)
        status = result.exit_status
        if status:
            test.fail(result.stderr)
    if vm_name:
        virsh.destroy(vm_name, debug=True, ignore_status=True)
    if params.get("orig_config_xml"):
        params.get("orig_config_xml").sync()


def run(test, params, env):
    """
    Test set/get secret value for a volume

    1) Positive testing
       1.1) set the private or public secret value
       1.2) get the public secret value
       1.3) set secret value with --file option
       1.4) set secret value with --file and --plain option
       1.5) set secret value with --interactive
    2) Negative testing
       2.1) get private secret
       2.2) get secret without setting secret value
       2.3) get or set secret with invalid options
       2.4) set secret with doesn't exist UUID
    """
    def attach_disk_secret(params):
        """
        Attach a disk with secret to VM

        :params: the parameter dictionary
        :raise: test.fail when disk cannot be attached
        """
        secret_string = params.get("secret_base64_no_encoded")
        target_dev = params.get("target_dev", "vdb")
        uuid = params.get("secret_uuid")
        # TODO: support encoded data
        extra = "--object secret,id=sec0,data=%s -o key-secret=sec0" % secret_string
        tmp_dir = data_dir.get_tmp_dir()
        disk_path = os.path.join(tmp_dir, "test.img")
        libvirt.create_local_disk("file", disk_format="luks",
                                  path=disk_path, size="1", extra=extra)
        new_disk_dict = {}
        new_disk_dict.update(
            {"source_encryption_dict": {"encryption": 'luks',
             "secret": {"type": "passphrase", "uuid": uuid}}})

        result = libvirt.attach_additional_device(vm_name, target_dev,
                                                  disk_path, new_disk_dict)
        if result.exit_status:
            raise test.fail("Attach device %s failed." % target_dev)

    def check_vm_start(params):
        """
        Start a guest with a secret

        :params: the parameter dictionary
        :raise: test.fail when VM cannot be started
        """
        attach_disk_secret(params)

        if not vm.is_alive():
            try:
                vm.start()
            except virt_vm.VMStartError as err:
                test.fail("Failed to start VM: %s" % err)

    # Run test case
    uuid = ""
    vm_name = params.get("main_vm")
    if vm_name:
        vm = env.get_vm(vm_name)
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        params["orig_config_xml"] = vmxml.copy()

    usage_volume = params.get("secret_usage_volume",
                              "/var/lib/libvirt/images/foo-bar.secret")
    set_secret = params.get("set_secret", "yes")
    get_secret = params.get("get_secret", "yes")
    test_vm_start = params.get("test_vm_start", "no")

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

    if set_secret == "yes":
        if not libvirt_version.version_compare(6, 2, 0):
            if params.get("secret_file", "no") == "yes":
                test.cancel("Current libvirt version doesn't support "
                            "'--file' for virsh secret-set-value.")
            if "interactive" in params.get("set_secret_options", ""):
                test.cancel("Current libvirt version doesn't support "
                            "'--interactive' for virsh secret-set-value.")

    # positive and negative testing #########
    try:
        if set_secret == "yes":
            set_secret_value(test, params)
        if get_secret == "yes":
            get_secret_value(test, params)
        if test_vm_start == "yes":
            check_vm_start(params)
    finally:
        cleanup(test, params)
