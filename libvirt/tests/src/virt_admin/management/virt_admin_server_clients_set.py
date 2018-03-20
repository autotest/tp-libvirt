import logging
from virttest import virt_admin
from virttest import virsh
from virttest import utils_libvirtd
from virttest import utils_config


def run(test, params, env):
    """
    Test virt-admin  server-clients-set
    2) Change max_clients to a new value;
    3) get the current clients info;
    4) check whether the clients info is correct;
    5) try to connect other client onto the server;
    6) check whether the above connection status is correct.
    """

    server_name = params.get("server_name")
    options_ref = params.get("options_ref")
    nclients_max = params.get("nclients_maxi", "5000")
    nclients = params.get("nclients", "0")
    nclients_unauth_max = params.get("nclients_unauth_maxi", "")
    connect_able = params.get("connect_able", "yes")

    config = utils_config.LibvirtdConfig()
    libvirtd = utils_libvirtd.Libvirtd()
    virsh_instance = []

    def clients_info(server):
        """
        check the attributes by server-clients-set.
        1) get the output  returned by server-clients-set;
        2) split the output to get a dictionary of those attributes;
        :params server: print the info of the clients connecting to this server
        :return: a dict obtained by transforming the result_info
        """
        result_info = vp.srv_clients_info(server, ignore_status=True,
                                          debug=True)
        out = result_info.stdout.strip().splitlines()
        out_split = [item.split(':') for item in out]
        out_dict = dict([[item[0].strip(), item[1].strip()] for item in out_split])
        return out_dict

    try:
        if "max_clients" in options_ref:
            if int(nclients_max) > int(nclients):
                vp = virt_admin.VirtadminPersistent()
                result = vp.srv_clients_set(server_name, max_clients=nclients_max,
                                            ignore_status=True, debug=True)
            elif int(nclients_max) == int(nclients):
                config.max_clients = nclients_max
                config.max_anonymous_clients = nclients_unauth_max
                libvirtd.restart()
                vp = virt_admin.VirtadminPersistent()
                nclients_max_set = int(nclients_max) + 3
                result = vp.srv_clients_set(server_name, max_clients=nclients_max_set,
                                            ignore_status=True, debug=True)
            else:
                vp = virt_admin.VirtadminPersistent()
                for _ in range(int(nclients)):
                    virsh_instance.append(virsh.VirshPersistent(uri='qemu:///system'))
                result = vp.srv_clients_set(server_name, max_clients=nclients_max,
                                            max_unauth_clients=nclients_unauth_max,
                                            ignore_status=True, debug=True)

            outdict = clients_info(server_name)
            if result.exit_status:
                test.fail("This operation should success "
                          "but failed! output:\n%s " % result)
            elif outdict["nclients_max"] != str(nclients_max_set):
                test.fail("attributes set by server-clients-set "
                          "is not correct!")

            try:
                virsh_instance.append(virsh.VirshPersistent(uri='qemu:///system'))
            except Exception as info:
                if connect_able == "yes":
                    test.fail("Connection not success, error:\n %s" % info)
                else:
                    logging.info("Connections should not success, correct test result!")
            else:
                if connect_able == "yes":
                    logging.info("Connections is successful, correct test result!")
                else:
                    test.fail("error: Connection should not success! "
                              "Check the attributes.")

    finally:
        config.restore()
        libvirtd.restart()
