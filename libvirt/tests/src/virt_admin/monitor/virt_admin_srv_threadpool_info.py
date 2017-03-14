from avocado.core import exceptions
from virttest import virt_admin
from virttest import utils_config
from virttest import utils_libvirtd


def run(test, params, env):
    """
    Test virt-admin srv-threadpool-info

    1) Change the threadpool related parameters in libvirtd.conf;
    2) Restart libvirtd daemon;
    3) Check whether the parameter value listed by srv-threadpool-info
       are the same with the above settings.
    """
    min_workers = params.get("min_workers")
    max_workers = params.get("max_workers")
    prio_workers = params.get("prio_workers")
    admin_min_workers = params.get("admin_min_workers")
    admin_max_workers = params.get("admin_max_workers")
    server_name = params.get("server_name")

    config = utils_config.LibvirtdConfig()
    libvirtd = utils_libvirtd.Libvirtd()

    try:
        if server_name == "libvirtd":
            config.min_workers = min_workers
            config.max_workers = max_workers
            config.prio_workers = prio_workers
        elif server_name == "admin":
            config.admin_min_workers = admin_min_workers
            config.admin_max_workers = admin_max_workers

        libvirtd.restart()
        vp = virt_admin.VirtadminPersistent()
        result = vp.srv_threadpool_info(server_name, ignore_status=True, debug=True)

        output = result.stdout.strip().splitlines()
        out_split = [item.split(':') for item in output]
        out_dict = dict([[item[0].strip(), item[1].strip()] for item in out_split])

        if result.exit_status:
            raise exceptions.TestFail("This operation should success "
                                      "but failed! Output: \n %s" % result)
        else:
            if server_name == "libvirtd":
                if not (out_dict["minWorkers"] == min_workers and
                        out_dict["maxWorkers"] == max_workers and
                        out_dict["prioWorkers"] == prio_workers):
                    raise exceptions.TestFail("attributes info listed by "
                                              "srv-threadpool-info is not correct!")
            elif server_name == "admin":
                if not (out_dict["minWorkers"] == admin_min_workers and
                        out_dict["maxWorkers"] == admin_max_workers):
                    raise exceptions.TestFail("attributes info listed by "
                                              "srv-threadpool-info is not correct!")
    finally:
        config.restore()
        libvirtd.restart()
