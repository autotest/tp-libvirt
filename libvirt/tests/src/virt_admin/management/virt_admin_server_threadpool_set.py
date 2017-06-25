from virttest import virt_admin
from virttest import utils_libvirtd


def run(test, params, env):
    """
    Test virt-admin server-threadpool-set

    1) Test the --min-workers options
       set the params of threadpool by virt-admin server-threadpool-set,
       check whether the result printed by server-threadpool-info
       are consistent with the above setting.
    """
    server_name = params.get("server_name")
    options_ref = params.get("options_ref")
    min_workers = params.get("min_workers")
    max_workers = params.get("max_workers")
    nworkers = params.get("nworkers")
    is_positive = params.get("is_positive")

    libvirtd = utils_libvirtd.Libvirtd()
    vp = virt_admin.VirtadminPersistent()

    def threadpool_info(server):
        """
        check the attributes by server-threadpool-set.
        1) get the output returned by server-threadpool-set;
        2) split the output to get a dictionary of those attributes.
        :param server: get the threadpool info of this server.
        :return: a dict obtained by converting result_info.
        """
        result_info = vp.srv_threadpool_info(server, ignore_status=True,
                                             debug=True)
        out = result_info.stdout.strip().splitlines()
        out_split = [item.split(':') for item in out]
        out_dict = dict([[item[0].strip(), item[1].strip()] for item in out_split])
        return out_dict

    try:
        if is_positive == "yes":
            if "min-workers" in options_ref:
                if int(min_workers) < int(nworkers):
                    result = vp.srv_threadpool_set(server_name, min_workers=min_workers,
                                                   ignore_status=True, debug=True)
            if "max-workers" in options_ref:
                if int(max_workers) > int(nworkers):
                    result = vp.srv_threadpool_set(server_name, max_workers=max_workers,
                                                   ignore_status=True, debug=True)

            outdict = threadpool_info(server_name)
            if result.exit_status:
                test.fail("This operation should success "
                          "but failed! output:\n%s" % result)
            if "min-workers" in options_ref:
                if outdict["minWorkers"] != min_workers:
                    test.fail("minWorkers set by server-threadpool-set "
                              "is not correct!")
            elif "max-workers" in options_ref:
                if outdict["maxWorkers"] != max_workers:
                    test.fail("maxWorkers set by server-threadpool-set "
                              "is not correct!")
        else:
            if "min_workers" in options_ref:
                if int(min_workers) > int(max_workers):
                    result = vp.srv_threadpool_set(server_name, min_workers=min_workers,
                                                   ignore_status=True, debug=True)

                if not result.exit_status:
                    test.fail("This operation should fail but succeeded!")

    finally:
        libvirtd.restart()
