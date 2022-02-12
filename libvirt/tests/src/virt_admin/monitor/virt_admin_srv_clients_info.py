from virttest import virt_admin
from virttest import utils_libvirtd
from virttest import utils_iptables
from virttest import virsh
from virttest.utils_conn import TLSConnection


def run(test, params, env):
    """
    Test virt-admin srv-clients-info

    1) Change the clients related parameters in daemon config file;
    2) Restart daemon;
    3) Start several virsh connections;
    4) Check whether the parameters value listed by srv-clients-info
       are the same with the above settings.
    """

    max_clients = params.get("max_clients")
    max_anonymous_clients = params.get("max_anonymous_clients")
    server_name = params.get("server_name")
    num_clients = params.get("num_clients")
    server_ip = params["server_ip"] = params.get("local_ip")
    server_user = params["server_user"] = params.get("local_user", "root")
    server_pwd = params["server_pwd"] = params.get("local_pwd")
    client_ip = params["client_ip"] = params.get("remote_ip")
    client_pwd = params["client_pwd"] = params.get("remote_pwd")
    client_user = params["server_user"] = params.get("remote_user", "root")
    tls_port = params.get("tls_port", "16514")
    tls_uri = "qemu+tls://%s:%s/system" % (server_ip, tls_port)
    tls_obj = None
    remote_virsh_dargs = {'remote_ip': client_ip, 'remote_user': client_user,
                          'remote_pwd': client_pwd, 'uri': tls_uri,
                          'ssh_remote_auth': True}
    if not server_name:
        server_name = virt_admin.check_server_name()

    config = virt_admin.managed_daemon_config()
    daemon = utils_libvirtd.Libvirtd("virtproxyd")

    try:
        config.max_clients = max_clients
        config.max_anonymous_clients = max_anonymous_clients
        daemon.restart()

        tls_obj = TLSConnection(params)
        tls_obj.conn_setup()
        tls_obj.auto_recover = True
        utils_iptables.Firewall_cmd().add_port(tls_port, 'tcp', permanent=True)

        clients_instant = []
        for _ in range(int(num_clients)):
            # Under split daemon mode, we can connect to virtproxyd via
            # remote tls/tcp connections,can not connect to virtproxyd direct
            # on local host
            clients_instant.append(virsh.VirshPersistent(**remote_virsh_dargs))

        result = virt_admin.srv_clients_info(server_name, ignore_status=True, debug=True)
        output = result.stdout_text.strip().splitlines()
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
        utils_iptables.Firewall_cmd().remove_port(tls_port, 'tcp', permanent=True)
