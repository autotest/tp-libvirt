from virttest import virt_admin
from virttest import utils_libvirtd
from virttest import virsh
from virttest import ssh_key


def run(test, params, env):
    """
    Test virt-admin srv-clients-info

    1) Change the clients related parameters in daemon config file;
    2) Restart daemon;
    3) Start several virsh connections;
    4) Check whether the parameters value listed by srv-clents-info
       are the same with the above settings.
    """
    max_clients = params.get("max_clients")
    max_anonymous_clients = params.get("max_anonymous_clients")
    server_name = params.get("server_name")
    num_clients = params.get("num_clients")
    local_pwd = params.get("local_pwd")

    if not server_name:
        server_name = virt_admin.check_server_name()

    config = virt_admin.managed_daemon_config()
    daemon = utils_libvirtd.Libvirtd()
    ssh_key.setup_remote_ssh_key("localhost", "root", local_pwd)

    try:
        config.max_clients = max_clients
        config.max_anonymous_clients = max_anonymous_clients
        daemon.restart()
        vp = virt_admin.VirtadminPersistent()

        virsh_instant = []
        for _ in range(int(num_clients)):
            # Under split daemon mode, we can connect to virtproxyd via
            # remote connections,can not connect to virtproxyd direct
            # on local host
            virsh_instant.append(virsh.VirshPersistent(
                uri="qemu+ssh://localhost/system"))

        result = vp.srv_clients_info(server_name, ignore_status=True, debug=True)
        output = result.stdout.strip().splitlines()
        out_split = [item.split(':') for item in output]
        out_dict = dict([[item[0].strip(), item[1].strip()] for item in out_split])

        if result.exit_status:
            test.fail("This operation should success "
                      "but failed. Output:\n %s" % result)
        else:
            if not (out_dict["nclients_max"] == max_clients and
                    out_dict["nclients_unauth_max"] == max_anonymous_clients):
                test.fail("attributes info listed by "
                          "srv-clients-info is not correct.")
            if not out_dict["nclients"] == num_clients:
                test.fail("the number of clients connect to daemon "
                          "is not correct.")
    finally:
        config.restore()
        daemon.restart()
