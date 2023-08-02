import os

from avocado.utils import process


def set_tpm_perms(vmxml, params):
    """
    Set the perms of swtpm lib to allow other users to write in the dir.

    :param vmxml: The vmxml object
    :param params: Dictionary with the test parameters
    """
    swtpm_lib = params.get("swtpm_lib")
    swtpm_perms_file = params.get("swtpm_perms_file")
    if vmxml.devices.by_device_tag('tpm'):
        cmd = "getfacl -R %s > %s" % (swtpm_lib, swtpm_perms_file)
        process.run(cmd, ignore_status=True, shell=True)
        cmd = "chmod -R 777 %s" % swtpm_lib
        process.run(cmd, ignore_status=False, shell=True)


def restore_tpm_perms(vmxml, params):
    """
    Restore the perms of swtpm lib.

    :param vmxml: The vmxml object
    :param params: Dictionary with the test parameters
    """
    swtpm_perms_file = params.get("swtpm_perms_file")
    if vmxml.devices.by_device_tag('tpm'):
        if os.path.isfile(swtpm_perms_file):
            cmd = "setfacl --restore=%s" % swtpm_perms_file
            process.run(cmd, ignore_status=True, shell=True)
            os.unlink(swtpm_perms_file)
