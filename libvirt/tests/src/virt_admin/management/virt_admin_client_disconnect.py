from virttest import virt_admin
from virttest import virsh
from virttest import utils_libvirtd
from virttest import utils_iptables
from virttest.utils_conn import TLSConnection


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

    daemon = utils_libvirtd.Libvirtd("virtproxyd")

    try:
        tls_obj = TLSConnection(params)
        tls_obj.conn_setup()
        tls_obj.auto_recover = True
        utils_iptables.Firewall_cmd().add_port(tls_port, 'tcp', permanent=True)

        clients_instant = []
        for _ in range(int(num_clients)):
            # Under split daemon mode, we can connect to virtproxyd via
            # remote tcp/tls connections,can not connect to virtproxyd direct
            # on local host
            clients_instant.append(virsh.VirshPersistent(**remote_virsh_dargs))

        out = virt_admin.srv_clients_list(server_name, ignore_status=True,
                                          debug=True)
        client_id = out.stdout_text.strip().splitlines()[-1].split()[0]
        result = virt_admin.client_disconnect(server_name, client_id,
                                              ignore_status=True, debug=True)

        if result.exit_status:
            test.fail("This operation should "
                      "success but failed. output: \n %s" % result)
        elif result.stdout.decode().strip().split()[1][1:-1] != client_id:
            test.fail("virt-admin did not "
                      "disconnect the correct client.")
    finally:
        daemon.restart()
        utils_iptables.Firewall_cmd().remove_port(tls_port, 'tcp', permanent=True)
