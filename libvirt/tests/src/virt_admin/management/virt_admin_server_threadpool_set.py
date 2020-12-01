import logging
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
    priority_workers = params.get("priority_workers")
    is_positive = params.get("is_positive") == "yes"
    min_workers_gt_nworkers = params.get("min_workers_gt_nworkers") == "yes"
    max_workers_gt_nworkers = params.get("max_workers_gt_nworkers") == "yes"
    options_test_together = params.get("options_test_together") == "yes"

    if not server_name:
        server_name = virt_admin.check_server_name()

    daemon = utils_libvirtd.Libvirtd()
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
        if options_ref:
            if "min-workers" in options_ref:
                result = vp.srv_threadpool_set(server_name, min_workers=min_workers,
                                               ignore_status=True, debug=True)
            if "max-workers" in options_ref:
                if not max_workers_gt_nworkers:
                    vp.srv_threadpool_set(server_name, min_workers=min_workers,
                                          ignore_status=True, debug=True)
                logging.debug("The current workers state of the daemon server is %s",
                              threadpool_info(server_name))
                result = vp.srv_threadpool_set(server_name, max_workers=max_workers,
                                               ignore_status=True, debug=True)
            if "priority-workers" in options_ref:
                result = vp.srv_threadpool_set(server_name,
                                               prio_workers=priority_workers,
                                               ignore_status=True, debug=True)
        elif options_test_together:
            result = vp.srv_threadpool_set(server_name,
                                           max_workers=max_workers,
                                           min_workers=min_workers,
                                           prio_workers=priority_workers,
                                           ignore_status=True, debug=True)

        if result.exit_status:
            if is_positive:
                test.fail("This operation should success "
                          "but failed! output:\n%s" % result)
            else:
                logging.debug("This is the expected failure for negative cases")
        else:
            if is_positive:
                outdict = threadpool_info(server_name)
                if options_ref:
                    if "min-workers" in options_ref:
                        if outdict["minWorkers"] != min_workers:
                            test.fail("minWorkers set by server-threadpool-set "
                                      "is not correct!")
                        if min_workers_gt_nworkers:
                            if outdict["nWorkers"] != min_workers:
                                test.fail("nworkers is not increased as min-workers increased.")
                    if "max-workers" in options_ref:
                        if outdict["maxWorkers"] != max_workers:
                            test.fail("maxWorkers set by server-threadpool-set "
                                      "is not correct!")
                        if not max_workers_gt_nworkers:
                            if outdict["nWorkers"] != max_workers:
                                test.fail("nworkers is not increased as max-workers decreased.")
                    if "priority_workers" in options_ref:
                        if outdict["prioWorkers"] != priority_workers:
                            test.fail("priority workers set by server-threadpool-set "
                                      "is not correct!")
                elif options_test_together:
                    if (outdict["minWorkers"] != min_workers or
                            outdict["maxWorkers"] != max_workers or
                            outdict["prioWorkers"] != priority_workers):
                        test.fail("The numbers of workers set together by server-threadpool-set "
                                  "are not correct!")
            else:
                test.fail("This operation should fail but succeeded!")
    finally:
        daemon.restart()
