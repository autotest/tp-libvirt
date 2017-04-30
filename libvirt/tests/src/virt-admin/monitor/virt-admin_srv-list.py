import re
from autotest.client.shared import error

from virttest import virtadmin


def run(test, params, env):
    """
    Test command: virt-admin srv-list.
    """
    server_name = params.get("server_name", "")
    vp = virtadmin.VirtadminPersistent()
    result = vp.srv_list(ignore_status=True, debug=True)
    output = result.stdout.strip()

    if result.exit_status:
        raise error.TestFail("This operation should success but failed!")
    else:
        if not re.search(server_name, output):
            raise error.TestFail("server %s is not listed! ", server_name)
