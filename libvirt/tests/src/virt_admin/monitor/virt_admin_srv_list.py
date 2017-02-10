import re
from avocado.core import exceptions
from virttest import virt_admin


def run(test, params, env):
    """
    Test command: virt-admin srv-list.

    1) execute virt-admin srv-list
    2) check whether the server names printed by the
       above execution are correct
    """
    server_name = params.get("server_name", "")
    vp = virt_admin.VirtadminPersistent()
    result = vp.srv_list(ignore_status=True, debug=True)
    output = result.stdout.strip()

    if result.exit_status:
        raise exceptions.TestFail("This operation should success "
                                  "but failed! output:\n%s " % result)
    else:
        if not re.search(server_name, output):
            raise exceptions.TestFail("server %s is not listed! " % server_name)
