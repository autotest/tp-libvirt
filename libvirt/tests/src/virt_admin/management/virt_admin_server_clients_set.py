import logging as log
from virttest import virt_admin
from virttest import virsh
from virttest import utils_libvirtd
from virttest import utils_iptables
from virttest.utils_conn import TLSConnection


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test virt-admin  server-clients-set
    2) Change max_clients to a new value;
    3) get the current clients info;
    4) check whether the clients info is correct;
    5) try to connect other client onto the server;
    6) check whether the above connection status is correct.
    """

    def clients_info(server):
        """
        check the attributes by server-clients-set.
        1) get the output  returned by server-clients-set;
        2) split the output to get a dictionary of those attributes;
        :params server: print the info of the clients connecting to this server
        :return: a dict obtained by transforming the result_info
        """
        result_info = virt_admin.srv_clients_info(server, ignore_status=True,
                                                  debug=True)
        out = result_info.stdout_text.strip().splitlines()
        out_split = [item.split(':') for item in out]
        out_dict = dict([[item[0].strip(), item[1].strip()] for item in out_split])
        return out_dict

    def chk_connect_to_daemon(connect_able):
        try:
            virsh_instance.append(virsh.VirshPersistent(**remote_virsh_dargs))
        except Exception as info:
            if connect_able == "yes":
                test.fail("Connection to daemon is not success, error:\n %s" % info)
            else:
                logging.info("Connections to daemon should not success, "
                             "this is a correct test result!")
        else:
            if connect_able == "yes":
                logging.info("Connections to daemon is successful, "
                             "this is a correct test result!")
            else:
                test.fail("error: Connection to daemon should not success! "
                          "Check the attributes.")

    server_name = params.get("server_name")
    is_positive = params.get("is_positive") == "yes"
    options_ref = params.get("options_ref")
    nclients_max = params.get("nclients_maxi")
    nclients = params.get("nclients")
    nclients_unauth_max = params.get("nclients_unauth_maxi")
    connect_able = params.get("connect_able")
    options_test_together = params.get("options_test_together")
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
    virsh_instance = []

    try:
        if nclients:
            tls_obj = TLSConnection(params)
            tls_obj.conn_setup()
            tls_obj.auto_recover = True
            utils_iptables.Firewall_cmd().add_port(tls_port, 'tcp', permanent=True)

        if options_ref:
            if "max-clients" in options_ref:
                if nclients:
                    if int(nclients_max) > int(nclients):
                        config.max_clients = nclients
                        config.max_anonymous_clients = nclients_unauth_max
                        daemon.restart()
                        for _ in range(int(nclients)):
                            virsh_instance.append(virsh.VirshPersistent(**remote_virsh_dargs))
                        result = virt_admin.srv_clients_set(server_name, max_clients=nclients_max,
                                                            ignore_status=True, debug=True)
                    elif int(nclients_max) <= int(nclients):
                        for _ in range(int(nclients)):
                            virsh_instance.append(virsh.VirshPersistent(**remote_virsh_dargs))
                        result = virt_admin.srv_clients_set(server_name, max_clients=nclients_max,
                                                            max_unauth_clients=nclients_unauth_max,
                                                            ignore_status=True, debug=True)

                else:
                    result = virt_admin.srv_clients_set(server_name, max_clients=nclients_max,
                                                        ignore_status=True, debug=True)
            elif "max-unauth-clients" in options_ref:
                result = virt_admin.srv_clients_set(server_name, max_unauth_clients=nclients_unauth_max,
                                                    ignore_status=True, debug=True)
        elif options_test_together:
            result = virt_admin.srv_clients_set(server_name, max_clients=nclients_max,
                                                max_unauth_clients=nclients_unauth_max,
                                                ignore_status=True, debug=True)

        outdict = clients_info(server_name)

        if result.exit_status:
            if is_positive:
                test.fail("This operation should success "
                          "but failed! output:\n%s " % result)
            else:
                logging.debug("This failure is expected!")
        else:
            if is_positive:
                if options_ref:
                    if "max-clients" in options_ref:
                        if outdict["nclients_max"] != nclients_max:
                            test.fail("attributes set by server-clients-set "
                                      "is not correct!")
                        if nclients:
                            chk_connect_to_daemon(connect_able)
                    elif "max_unauth_clients" in options_ref:
                        if outdict["nclients_unauth_max"] != nclients_unauth_max:
                            test.fail("attributes set by server-clients-set "
                                      "is not correct!")
                elif options_test_together:
                    if (outdict["nclients_max"] != nclients_max or
                            outdict["nclients_unauth_max"] != nclients_unauth_max):
                        test.fail("attributes set by server-clients-set "
                                  "is not correct!")
            else:
                test.fail("This is a negative case, should get failure.")
    finally:
        for session in virsh_instance:
            session.close_session()
        config.restore()
        daemon.restart()
        utils_iptables.Firewall_cmd().remove_port(tls_port, 'tcp', permanent=True)
