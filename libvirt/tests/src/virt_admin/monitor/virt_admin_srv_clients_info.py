from avocado.core import exceptions
from virttest import virt_admin
from virttest import utils_libvirtd
from virttest import utils_config
from virttest import virsh


def run(test, params, env):
    """
    Test virt-admin srv-clients-info

    1) Change the clients related parameters in libvirtd.conf;
    2) Restart libvirtd daemon;
    3) Start several virsh connections;
    4) Check whether the parameters value listed by srv-clents-info
       are the same with the above settings.
    """
    max_clients = params.get("max_clients")
    max_anonymous_clients = params.get("max_anonymous_clients")
    server_name = params.get("server_name")
    num_clients = params.get("num_clients")

    config = utils_config.LibvirtdConfig()
    libvirtd = utils_libvirtd.Libvirtd()

    try:
        config.max_clients = max_clients
        config.max_anonymous_clients = max_anonymous_clients
        libvirtd.restart()
        vp = virt_admin.VirtadminPersistent()

        virsh_instant = []
        for _ in range(int(num_clients)):
            virsh_instant.append(virsh.VirshPersistent(uri="qemu:///system"))

        result = vp.srv_clients_info(server_name, ignore_status=True, debug=True)
        output = result.stdout.strip().splitlines()
        out_split = [item.split(':') for item in output]
        out_dict = dict([[item[0].strip(), item[1].strip()] for item in out_split])

        if result.exit_status:
            raise exceptions.TestFail("This operation should success "
                                      "but failed. Output:\n %s" % result)
        else:
            if not (out_dict["nclients_max"] == max_clients and
                    out_dict["nclients_unauth_max"] == max_anonymous_clients):
                raise exceptions.TestFail("attributes info listed by "
                                          "srv-clients-info is not correct.")
            if not out_dict["nclients"] == num_clients:
                raise exceptions.TestFail("the number of clients connect to libvirtd "
                                          "is not correct.")
    finally:
        config.restore()
        libvirtd.restart()
