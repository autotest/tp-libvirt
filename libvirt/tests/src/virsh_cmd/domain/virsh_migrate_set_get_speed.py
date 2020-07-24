import logging

from virttest import ssh_key
from virttest import virsh
from virttest import libvirt_vm
from virttest import utils_test
from virttest import migration
from virttest import libvirt_version


UINT32_MAX = (1 << 32) - 1
INT64_MAX = (1 << 63) - 1
UINT64_MAX = (1 << 64) - 1

# The virsh migrate-setspeed indicates units are in MiB
# and not bytes, so adjust our values. This was not checked
# until libvirt 1.2.4 by commit id's 'c4206d7c' and 'dff3ad00'
# although it's been true since the code was initially added.
# This test thus must use proper values.  Additionally, code
# points out that max is an INT64, which is not called out in
# the man page (yet).
UINT32_MiB = UINT32_MAX // (1024 * 1024)
INT64_MiB = INT64_MAX // (1024 * 1024)
UINT64_MiB = UINT64_MAX // (1024 * 1024)
DEFAULT = INT64_MiB


def run(test, params, env):
    """
    Test command: virsh migrate-setspeed <domain> <bandwidth>
                  virsh migrate-getspeed <domain>.

    1) Prepare test environment.
    2) Try to set the maximum migration bandwidth (in MiB/s)
       for a domain through valid and invalid command.
    3) Recover test environment.
    4) Check result.
    """

    # MAIN TEST CODE ###
    # Process cartesian parameters
    vm_name = params.get("migrate_main_vm")
    bandwidth = params.get("bandwidth", "default")
    options_extra = params.get("options_extra", "")
    status_error = "yes" == params.get("status_error", "yes")
    virsh_dargs = {'debug': True}
    # Checking uris for migration
    twice_migration = "yes" == params.get("twice_migration", "no")
    if twice_migration:
        src_uri = params.get("migrate_src_uri",
                             "qemu+ssh://EXAMPLE/system")
        dest_uri = params.get("migrate_dest_uri",
                              "qemu+ssh://EXAMPLE/system")
        if src_uri.count('///') or src_uri.count('EXAMPLE'):
            test.cancel("The src_uri '%s' is invalid"
                        % src_uri)
        if dest_uri.count('///') or dest_uri.count('EXAMPLE'):
            test.cancel("The dest_uri '%s' is invalid"
                        % dest_uri)

    bz1083483 = False
    if bandwidth == "zero":
        expected_value = 0
    elif bandwidth == "one":
        expected_value = 1
    elif bandwidth == "negative":
        expected_value = -1
        bz1083483 = True
    elif bandwidth == "default":
        expected_value = DEFAULT
    elif bandwidth == "UINT32_MAX":
        expected_value = UINT32_MiB
    elif bandwidth == "INT64_MAX":
        expected_value = INT64_MiB
    elif bandwidth == "UINT64_MAX":
        expected_value = UINT64_MiB
        bz1083483 = True
    elif bandwidth == "INVALID_VALUE":
        expected_value = INT64_MiB + 1
        bz1083483 = True
    else:
        expected_value = bandwidth

    orig_value = virsh.migrate_getspeed(vm_name).stdout.strip()

    def set_get_speed(vm_name, expected_value, status_error=False,
                      options_extra="", **virsh_dargs):
        """Set speed and check its result"""
        result = virsh.migrate_setspeed(vm_name, expected_value,
                                        options_extra, **virsh_dargs)
        status = result.exit_status
        err = result.stderr.strip()

        # Check status_error
        if status_error:
            if status == 0 or err == "":
                # Without code for bz1083483 applied, this will succeed
                # when it shouldn't be succeeding.
                if bz1083483 and not libvirt_version.version_compare(1, 2, 4):
                    test.cancel("bz1083483 should result in fail")
                else:
                    test.fail("Expect fail, but run successfully!")

            # no need to perform getspeed if status_error is true
            return
        else:
            if status != 0 or err != "":
                test.fail("Run failed with right "
                          "virsh migrate-setspeed command")

        result = virsh.migrate_getspeed(vm_name, **virsh_dargs)
        status = result.exit_status
        actual_value = result.stdout.strip()
        err = result.stderr.strip()

        if status != 0 or err != "":
            test.fail("Run failed with virsh migrate-getspeed")

        logging.info("The expected bandwidth is %s MiB/s, "
                     "the actual bandwidth is %s MiB/s"
                     % (expected_value, actual_value))

        if int(actual_value) != int(expected_value):
            test.fail("Bandwidth value from getspeed "
                      "is different from expected value "
                      "set by setspeed")

    def verify_migration_speed(test, params, env):
        """
        Check if migration speed is effective with twice migration.
        """
        vms = env.get_all_vms()
        src_uri = params.get("migrate_src_uri", "qemu+ssh://EXAMPLE/system")
        dest_uri = params.get("migrate_dest_uri", "qemu+ssh://EXAMPLE/system")

        if not len(vms):
            test.cancel("Please provide migrate_vms for test.")

        if src_uri.count('///') or src_uri.count('EXAMPLE'):
            test.cancel("The src_uri '%s' is invalid" % src_uri)

        if dest_uri.count('///') or dest_uri.count('EXAMPLE'):
            test.cancel("The dest_uri '%s' is invalid" % dest_uri)

        remote_host = params.get("migrate_dest_host")
        username = params.get("migrate_dest_user", "root")
        password = params.get("migrate_dest_pwd")
        # Config ssh autologin for remote host
        ssh_key.setup_ssh_key(remote_host, username, password, port=22)

        # Check migrated vms' state
        for vm in vms:
            if vm.is_dead():
                vm.start()

        load_vm_names = params.get("load_vms").split()
        # vms for load
        load_vms = []
        for vm_name in load_vm_names:
            load_vms.append(libvirt_vm.VM(vm_name, params, test.bindir,
                                          env.get("address_cache")))
        params["load_vms"] = load_vms

        bandwidth = int(params.get("bandwidth", "4"))
        stress_type = params.get("stress_type", "load_vms_booting")
        migration_type = params.get("migration_type", "orderly")
        thread_timeout = int(params.get("thread_timeout", "60"))
        delta = float(params.get("allowed_delta", "0.1"))
        virsh_migrate_timeout = int(params.get("virsh_migrate_timeout", "60"))
        # virsh migrate options
        virsh_migrate_options = "--live --unsafe --timeout %s" % virsh_migrate_timeout
        # Migrate vms to remote host
        mig_first = migration.MigrationTest()
        virsh_dargs = {"debug": True}
        for vm in vms:
            set_get_speed(vm.name, bandwidth, virsh_dargs=virsh_dargs)
            vm.wait_for_login()
        utils_test.load_stress(stress_type, params=params, vms=vms)
        mig_first.do_migration(vms, src_uri, dest_uri, migration_type,
                               options=virsh_migrate_options, thread_timeout=thread_timeout)
        for vm in vms:
            mig_first.cleanup_dest_vm(vm, None, dest_uri)
            # Keep it clean for second migration
            if vm.is_alive():
                vm.destroy()

        # Migrate vms again with new bandwidth
        second_bandwidth = params.get("second_bandwidth", "times")
        if second_bandwidth == "half":
            second_bandwidth = bandwidth / 2
            speed_times = 2
        elif second_bandwidth == "times":
            second_bandwidth = bandwidth * 2
            speed_times = 0.5
        elif second_bandwidth == "same":
            second_bandwidth = bandwidth
            speed_times = 1

        # Migrate again
        for vm in vms:
            if vm.is_dead():
                vm.start()
            vm.wait_for_login()
            set_get_speed(vm.name, second_bandwidth, virsh_dargs=virsh_dargs)
        utils_test.load_stress(stress_type, params=params, vms=vms)
        mig_second = migration.MigrationTest()
        mig_second.do_migration(vms, src_uri, dest_uri, migration_type,
                                options=virsh_migrate_options, thread_timeout=thread_timeout)
        for vm in vms:
            mig_second.cleanup_dest_vm(vm, None, dest_uri)

        fail_info = []
        # Check whether migration failed
        if len(fail_info):
            test.fail(fail_info)

        for vm in vms:
            first_time = mig_first.mig_time[vm.name]
            second_time = mig_second.mig_time[vm.name]
            logging.debug("Migration time for %s:\n"
                          "Time with Bandwidth '%s' first: %s\n"
                          "Time with Bandwidth '%s' second: %s", vm.name,
                          bandwidth, first_time, second_bandwidth, second_time)
            shift = float(abs(first_time * speed_times - second_time)) / float(second_time)
            logging.debug("Shift:%s", shift)
            if delta < shift:
                fail_info.append("Spent time for migrating %s is intolerable." % vm.name)

        # Check again for speed result
        if len(fail_info):
            test.fail(fail_info)

    # Run test case
    try:
        set_get_speed(vm_name, expected_value, status_error,
                      options_extra, **virsh_dargs)
        if twice_migration:
            verify_migration_speed(test, params, env)
        else:
            set_get_speed(vm_name, expected_value, status_error,
                          options_extra, **virsh_dargs)
    finally:
        #restore bandwidth to default
        virsh.migrate_setspeed(vm_name, orig_value)
        if twice_migration:
            for vm in env.get_all_vms():
                migration.MigrationTest().cleanup_dest_vm(vm, src_uri, dest_uri)
                if vm.is_alive():
                    vm.destroy(gracefully=False)
