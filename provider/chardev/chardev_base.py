# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Nan Li <nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import re

from avocado.utils import process


def send_message(vm, send_on="host", send_msg="Test message", send_path=""):
    """
    Send message

    :params: vm: guest object
    :params: send_on: send on terminal
    :params: send_msg: send message content
    :params: send_path: the send path on terminal
    """
    if send_on == "host":
        cmd = "echo %s > %s" % (send_msg, send_path)
        process.run(cmd, shell=True)
    elif send_on == "guest":
        if not vm.is_alive():
            vm.start()
        session = vm.wait_for_login()
        session.cmd_status_output("echo %s > %s " % (send_msg, send_path))
        session.close()


def get_match_count(test, check_path, key_message):
    """
    Get expected messages count in path

    :param check_path: file path to be checked
    :param key_message: key message that needs to be captured
    :return count: the count of key message
    """
    count = 0
    try:
        with open(check_path, 'r', encoding='ISO-8859-1') as fp:
            for line in fp.readlines():
                if re.findall(key_message, line):
                    count += 1
                    test.log.debug("Get '%s' in %s %s times" % (
                        key_message, check_path, str(count)))
    except IOError:
        test.fail("Failed to read %s!" % check_path)

    return count
