from virttest import virsh

from virttest.utils_test import libvirt

from provider.migration import base_steps


def get_bandwidth(vm_name, vm_status, postcopy_options, error_msg, extra, test):
    """
    Get bandwidth

    :param vm_name: vm name
    :param vm_status: vm status
    :param postcopy_options: postcopy options
    :param error_msg: error message
    :param extra: extra options
    :param test: test object
    :return: return bandwidth or None
    """
    ret = virsh.migrate_getspeed(vm_name, extra=extra)
    if ret.exit_status:
        if postcopy_options and vm_status == "vm_shutoff":
            libvirt.check_result(ret, error_msg)
            return None
        else:
            test.fail("Get bandwidth fail.")
    return int(ret.stdout_text.strip())


def run_test(params, vm_name, test):
    """
    Run test for setspeed/getspeed

    :param params: Dictionary with the test parameters
    :param vm_name: vm name
    :param test: test object
    """
    bandwidth = params.get("bandwidth")
    status_error = "yes" == params.get('status_error', 'no')
    error_msg = params.get("err_msg")
    vm_status = params.get("vm_status")
    postcopy_options = params.get("postcopy_options")
    error_msg_1 = params.get("err_msg_1")

    test.log.info("Run test for migrate-setspeed/migrate-getspeed.")
    if postcopy_options:
        extra = postcopy_options
    else:
        extra = None

    orig_bandwidth = get_bandwidth(vm_name, vm_status, postcopy_options,
                                   error_msg_1, extra, test)
    ret = virsh.migrate_setspeed(vm_name, bandwidth, extra=extra, debug=True)
    if ret.exit_status:
        if postcopy_options and vm_status == "vm_shutoff":
            if bandwidth in ["-1", "8796093022208", "17592186044416"]:
                libvirt.check_result(ret, error_msg)
            else:
                libvirt.check_result(ret, error_msg_1)
            return
        else:
            if status_error:
                libvirt.check_result(ret, error_msg)
            else:
                test.fail("Set bandwidth fail.")

    new_bandwidth = get_bandwidth(vm_name, vm_status, postcopy_options,
                                  error_msg_1, extra, test)
    if bandwidth in ["-1", "8796093022208", "17592186044416"]:
        if orig_bandwidth and new_bandwidth and orig_bandwidth != new_bandwidth:
            test.fail("For bandwidth=%s, bandwidth should keep its original value." % bandwidth)
    else:
        if int(bandwidth) != new_bandwidth:
            test.fail("Set bandwidth fail: %s" % new_bandwidth)


def run(test, params, env):
    """
    To verify that migration bandwidth limit can be set/got by
    migrate-setspeed/getspeed.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    vm_name = params.get("migrate_main_vm")

    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)
    migration_obj.setup_default()

    try:
        run_test(params, vm_name, test)
    finally:
        migration_obj.cleanup_default()
