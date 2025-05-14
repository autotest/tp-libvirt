import logging
import re
import ast

from avocado.utils import process

from virttest import libvirt_version


LOGGER = logging.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Check libvirt/qemu/kvm user/group are created via sysuser.d
    """
    libvirt_version.is_libvirt_feature_supported(params)
    users_list = ast.literal_eval(params.get("users_list", "[]"))

    users_match_list = []

    sysuser_dir = '/usr/lib/sysusers.d/'
    cmd = "grep -hr -E 'kvm|qemu|libvirt' %s" % sysuser_dir
    output = process.getoutput(cmd, shell=True)
    LOGGER.debug(output)

    for user in users_list:
        if not re.search(user, output):
            test.fail("%s not created by sysuser.d" % user)
