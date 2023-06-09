from virttest import virsh

from provider.migration import base_steps


def run(test, params, env):
    """
    Test cases about migration performance tuning.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    def setup_auto_converge():
        """
        Setup for auto converge case

        """
        test.log.info("Setup for auto converge case.")
        # Set maxdowntime to small value, then migration won't converge too fast.
        virsh.migrate_setmaxdowntime(vm_name, "100", debug=True)
        migration_obj.setup_connection()

    def setup_parallel_connections():
        """
        Setup for parallel connections

        """
        test.log.info("Setup for parallel connections.")
        parallel_conn_options = params.get("parallel_conn_options")
        if parallel_conn_options:
            extra = params.get("virsh_migrate_extra")
            extra = "%s %s" % (extra, parallel_conn_options)
            params.update({"virsh_migrate_extra": extra})
        migration_obj.setup_connection()

    def verify_memory_compression():
        """
        Verify for memory compression

        """
        cache_size = params.get("cache_size")
        check_compression_list = eval(params.get("check_compression_list"))
        test.log.debug("Start verify compression cache.")
        ret = virsh.domjobinfo(vm_name, extra=" --completed", debug=True, ignore_status=True)
        if ret.exit_status:
            test.fail("Failed to get domjobinfo --completed: %s" % ret.stderr)
        jobinfo = ret.stdout_text
        for name in check_compression_list:
            if name not in jobinfo:
                test.fail("Not found '%s' in domjobinfo." % name)
        if cache_size:
            comp_info = "Compression cache: %.3f MiB" % (int(cache_size)/(1024*1024))
            if comp_info not in jobinfo:
                test.fail("Not found '%s' in domjobinfo." % comp_info)

    test_case = params.get('test_case', '')
    vm_name = params.get("migrate_main_vm")

    vm = env.get_vm(vm_name)
    params.update({'vm_obj': vm})
    migration_obj = base_steps.MigrationBase(test, vm, params)
    setup_test = eval("setup_%s" % test_case) if "setup_%s" % test_case in \
        locals() else migration_obj.setup_connection
    verify_test = eval("verify_%s" % test_case) if "verify_%s" % test_case in \
        locals() else migration_obj.verify_default

    try:
        setup_test()
        migration_obj.run_migration()
        verify_test()
    finally:
        migration_obj.cleanup_connection()
