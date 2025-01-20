import re

from avocado.utils import process

from virttest import utils_libvirtd
from virttest import virt_admin


def run(test, params, env):
    """
    Test command: virt-admin srv-list.

    1) execute virt-admin srv-list
    2) check whether the server names printed by the
       above execution are correct
    """
    server_name = params.get("server_name")
    if not server_name:
        server_name = virt_admin.check_server_name()

    process.run("systemctl status virtproxyd-admin.socket", shell=True)
    process.run("systemctl status virtproxyd", shell=True)
    virtproxyd_admin_socket = utils_libvirtd.DaemonSocket("virtproxyd-admin.socket")
    virtproxyd_admin_socket.restart()
    vp = virt_admin.VirtadminPersistent()
    result = vp.srv_list(ignore_status=True, debug=True)
    output = result.stdout.strip()

    if result.exit_status:
        test.fail("This operation should success "
                  "but failed! output:\n%s " % result)
    else:
        if not re.search(server_name, output):
            test.fail("server %s is not listed! " % server_name)
