# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Nan Li <nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
from avocado.utils import process


def check_audit_log(test, key_message=None):
    """
    Check key messages in log file

    :param test: test object instance
    :param key_message: key message that needs to be captured
    """
    for item in key_message:
        cmd = 'ausearch --start recent -m VIRT_RESOURCE -i | grep %s' % item
        result = process.run(cmd, ignore_status=True, shell=True)
        if result.exit_status:
            test.fail("Check %s failed in %s" % (item, cmd))
        else:
            test.log.debug("Check %s exist", item)
