from virttest.libvirt_xml import vm_xml

from provider.migration import base_steps


def run(test, params, env):
    """
    Test migration with different CPU topology configurations

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment
    """

    def verify_guest_topology(vm_obj, expected_threads, test, use_serial=False):
        """Verify CPU topology in guest OS"""
        if use_serial:
            vm_obj.cleanup_serial_console()
            vm_obj.create_serial_console()
            session = vm_obj.wait_for_serial_login(timeout=360)
        else:
            session = vm_obj.wait_for_login(timeout=360)

        try:
            cmd = "lscpu | grep 'Thread(s) per core' | awk '{print $NF}'"
            output = session.cmd_output(cmd).strip()
            actual_threads = int(output)

            test.log.info("Expected threads per core: %s", expected_threads)
            test.log.info("Actual threads per core in guest: %s", actual_threads)

            if actual_threads != expected_threads:
                test.fail("Thread count mismatch! Expected: %s, Got: %s" %
                          (expected_threads, actual_threads))

            cmd = "lscpu | grep '^CPU(s):' | head -1 | awk '{print $NF}'"
            total_cpus = int(session.cmd_output(cmd).strip())
            test.log.info("Total CPUs in guest: %s", total_cpus)

            test.log.info("CPU topology verified: %s CPUs, %s threads per core",
                          total_cpus, expected_threads)
        finally:
            session.close()
            if use_serial:
                vm_obj.cleanup_serial_console()

    def setup_test():
        """Setup CPU topology configuration"""
        vcpu_sockets = int(params.get("topology_sockets", "1"))
        vcpu_cores = int(params.get("topology_cores", "1"))
        vcpu_threads = int(params.get("topology_threads", "1"))

        test.log.info("Setup steps - configuring CPU topology")
        migration_obj.setup_connection()

        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

        if vm.is_alive():
            vm.destroy(gracefully=False)

        test.log.info("Configuring CPU topology: sockets=%s, cores=%s, threads=%s",
                      vcpu_sockets, vcpu_cores, vcpu_threads)

        vmxml.vcpu = vcpu_sockets * vcpu_cores * vcpu_threads

        cpu_xml = vmxml.cpu
        cpu_xml.topology = {
            "sockets": str(vcpu_sockets),
            "cores": str(vcpu_cores),
            "threads": str(vcpu_threads)
        }
        vmxml.cpu = cpu_xml
        vmxml.sync()

        test.log.debug("VM XML after topology configuration")

        if not vm.is_alive():
            vm.start()
            vm.wait_for_login(timeout=360).close()

        test.log.info("Verifying CPU topology before migration...")
        verify_guest_topology(vm, vcpu_threads, test, use_serial=False)

    def verify_test():
        """Verify CPU topology after migration"""
        vcpu_threads = int(params.get("topology_threads", "1"))
        dest_uri = params.get("virsh_migrate_desturi")

        test.log.info("Verifying CPU topology after migration...")

        # Change connect_uri to destination and use serial console
        backup_uri = migration_obj.vm.connect_uri
        migration_obj.vm.connect_uri = dest_uri

        try:
            verify_guest_topology(migration_obj.vm, vcpu_threads, test, use_serial=True)
        finally:
            migration_obj.vm.connect_uri = backup_uri

        migration_obj.verify_default()

        test.log.info("All topology verifications passed!")

    vm_name = params.get("migrate_main_vm")
    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)

    try:
        setup_test()
        migration_obj.run_migration()
        verify_test()
    finally:
        migration_obj.cleanup_connection()
