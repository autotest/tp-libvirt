"""Helper functions for virtio device testing"""

import aexpect
import logging as log

from avocado.utils import process

logging = log.getLogger('avocado.' + __name__)


def check_guest_dump(session, exists=True):
    """
    Check guest with hexdump

    :param session: ssh session to guest
    :param exists: check rng device exists/not exists
    :raises Exception: Exception is raised with information on what went wrong
    """
    check_cmd = "hexdump /dev/hwrng -n 100"
    try:
        status = session.cmd_status(check_cmd, 60)

        if status != 0 and exists:
            raise Exception("Fail to check hexdump in guest")
        if not exists:
            logging.info("hexdump cmd failed as expected")
    except aexpect.exceptions.ShellTimeoutError:
        if not exists:
            raise Exception("Still can find rng device in guest")
        logging.info("Hexdump did not fail with error")


def check_host(backend_dev):
    """
    Check random device on host, by looking if the device is used by qemu

    :params backend_dev: Device file that is expected to be claimed by qemu
    :raises Exception: Exception is raised with information on what went wrong
    """
    cmd = "lsof |grep %s" % backend_dev
    ret = process.run(cmd, ignore_status=True, shell=True)
    if ret.exit_status or not ret.stdout_text.count("qemu"):
        raise Exception("Failed to check random device"
                        " on host, command output: %s" % ret.stdout_text)


def remove_key(dictionary, key):
    """
    Helper function that goes recursively through a dictionary removing
    the designated key.

    :param dictionary: Dict to go through
    :param key: The key to remove
    """
    for dict_key in dictionary:
        if isinstance(dictionary[dict_key], dict):
            remove_key(dictionary[dict_key], key)
    dictionary.pop(key, None)


def comp_rng_xml(vmxml, rng_dict, remove_keys=None, status_error=False):
    """
    Compare rng xml from VM xml to rng dictionary on all attributes.

    :params vmxml: Vmxml object from where the device should be taken
    :params rng_dict: Rng device dictionary to compare
    :params remove_keys: Array of keys to remove from VM XML before comparison
    :params status_error: Set to True if you expect the test to fail
    :raises Exception: Exception in case the test fails
    """
    rng_device = vmxml.get_devices("rng")[0]
    rng_dev_attributes = rng_device.fetch_attrs()
    if remove_keys:
        for key in remove_keys:
            remove_key(rng_dev_attributes, key)
    for key, val, in rng_dict.items():
        if rng_dev_attributes[key] != val and status_error is False:
            raise Exception("Device XML value: '%s' does not match"
                            "the entry '%s' in VMXML" % (val, rng_dev_attributes[key]))
    if status_error is True:
        raise Exception("Rng device XML matches entry in VMXML, "
                        "but error was expected")
