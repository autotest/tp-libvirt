from virttest import virsh

from virttest.utils_test import libvirt

from provider.migration import base_steps


def run(test, params, env):
    """
    To verify that vm max downtime during live migration can be set/got by
    migrate-setmaxdowntime/getmaxdowntime.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """

    def run_test():
        """
        Run test for setmaxdowntime/getmaxdowntime

        """
        downtime = params.get("downtime")
        status_error = "yes" == params.get('status_error', 'no')
        error_msg = params.get("err_msg")
        vm_status = params.get("vm_status")

        test.log.info("Run test for migrate-setmaxdowntime/migrate-getmaxdowntime.")
        if vm_status == "vm_running":
            orig_maxdowntime = int(virsh.migrate_getmaxdowntime(vm_name).stdout_text.strip())
            ret = virsh.migrate_setmaxdowntime(vm_name, downtime, debug=True)
            if ret.exit_status:
                if status_error and downtime == "0":
                    libvirt.check_result(ret, error_msg)
                else:
                    test.fail("Set downtime fail.")
            new_maxdowntime = int(virsh.migrate_getmaxdowntime(vm_name).stdout_text.strip())
            if downtime == "0":
                if orig_maxdowntime != new_maxdowntime:
                    test.fail("For downtime=0, maxdowntime should keep its original value.")
            else:
                if int(downtime) != new_maxdowntime:
                    test.fail("Set maxdowntime fail: %s" % new_maxdowntime)
        elif vm_status == "vm_shutoff":
            ret = virsh.migrate_getmaxdowntime(vm_name, debug=True)
            if ret.exit_status:
                libvirt.check_result(ret, error_msg)
            else:
                test.fail("Expect to fail but succeed: migrate_getmaxdowntime")
            ret = virsh.migrate_setmaxdowntime(vm_name, downtime, debug=True)
            if ret.exit_status:
                libvirt.check_result(ret, error_msg)
            else:
                test.fail("Expect to fail but succeed: migrate_setmaxdowntime")

    vm_name = params.get("migrate_main_vm")

    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)
    migration_obj.setup_default()

    try:
        run_test()
    finally:
        migration_obj.cleanup_default()
