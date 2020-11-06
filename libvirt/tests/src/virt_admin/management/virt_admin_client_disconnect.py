from virttest import virt_admin
from virttest import virsh
from virttest import utils_libvirtd
from virttest import ssh_key


def run(test, params, env):
    """
    Test virt-admin client-disconnect

    1) Start several virsh connections;
    2) disconnect some connections;
    3) check whether virsh gives out the
       correct error messages;
    4) check whether srv_clients_info gives out the
       correct info about the virsh clients.
    """
    num_clients = params.get("num_clients")
    server_name = params.get("server_name")
    vp = virt_admin.VirtadminPersistent()
    daemon = utils_libvirtd.Libvirtd()
    local_pwd = params.get("local_pwd")

    if not server_name:
        server_name = virt_admin.check_server_name()
    ssh_key.setup_remote_ssh_key("localhost", "root", local_pwd)

    try:
        virsh_instant = []
        for _ in range(int(num_clients)):
            virsh_instant.append(virsh.VirshPersistent(
                uri="qemu+ssh://localhost/system"))

        out = vp.srv_clients_list(server_name,
                                  ignore_status=True, debug=True)
        client_id = out.stdout.strip().splitlines()[-1].split()[0]
        result = vp.client_disconnect(server_name, client_id,
                                      ignore_status=True, debug=True)

        if result.exit_status:
            test.fail("This operation should "
                      "success but failed. output: \n %s" % result)
        elif result.stdout.strip().split()[1][1:-1] != client_id:
            test.fail("virt-admin did not "
                      "disconnect the correct client.")
    finally:
        daemon.restart()
