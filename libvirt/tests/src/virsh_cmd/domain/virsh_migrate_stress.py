import logging

from virttest import libvirt_vm
from virttest import utils_test
from virttest.libvirt_xml import vm_xml
from virttest.staging import utils_memory


def set_cpu_memory(vm_name, cpu, memory):
    """
    Change vms' cpu and memory.

    :param vm_name: VM Name
    :param cpu: No of vcpus to be configured
    :param memory: Memory for VM to be configured
    """
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    vmxml.vcpu = cpu
    # To avoid exceeded current memory
    vmxml.max_mem = memory
    vmxml.current_mem = memory
    logging.debug("VMXML info:\n%s", vmxml.get('xml'))
    vmxml.sync()


def do_stress_migration(vms, srcuri, desturi, stress_type,
                        migration_type, test, params,
                        thread_timeout=60):
    """
    Migrate vms with stress.

    :param vms: migrated vms.
    :param srcuri: connect uri for source machine
    :param desturi: connect uri for destination machine
    :param stress_type: type of stress test in VM
    :param migration_type: type of migration to be performed
    :param params: Test dict params
    :param thread_timeout: default timeout for migration thread

    :raise: test.fail if migration fails
    """
    migrate_setup = utils_test.libvirt.MigrationTest()
    options = params.get("migrate_options")
    ping_count = int(params.get("ping_count", 10))
    vm_state = params.get("virsh_migrated_vm_state", "running")
    uptime = {}
    migrated_uptime = {}

    for vm in vms:
        session = vm.wait_for_login()
        uptime[vm.name] = vm.uptime()
        logging.info("uptime of VM %s: %s", vm.name, uptime[vm.name])
        migrate_setup.ping_vm(vm, test, params, ping_count=ping_count)
    logging.debug("Starting migration...")
    migrate_options = ("%s --timeout %s"
                       % (options, params.get("virsh_migrate_timeout", 60)))
    try:
        migrate_setup.do_migration(vms, srcuri, desturi, migration_type,
                                   options=migrate_options,
                                   thread_timeout=thread_timeout)
    except Exception, info:
        test.fail(info)
    for vm in vms:
        vm.connect_uri = desturi
        session = vm.wait_for_serial_login()
        migrated_uptime[vm.name] = vm.uptime(connect_uri=desturi)
        logging.info("uptime of migrated VM %s: %s", vm.name,
                     migrated_uptime[vm.name])
        if migrated_uptime[vm.name] < uptime[vm.name]:
            test.fail("vm went for a reboot during migration")
        if not migrate_setup.check_vm_state(vm, vm_state, desturi):
            test.fail("Migrated VMs failed to be in %s state at "
                      "destination" % vm_state)
        logging.info("Guest state is '%s' at destination is as expected",
                     vm_state)
        migrate_setup.ping_vm(vm, test, params, uri=desturi,
                              ping_count=ping_count)

    vm.connect_uri = None


def run(test, params, env):
    """
    Test migration under stress.
    """
    vm_names = params.get("migration_vms").split()
    if len(vm_names) < 2:
        test.cancel("Provide enough vms for migration")

    src_uri = libvirt_vm.complete_uri(params.get("migrate_source_host",
                                                 "EXAMPLE"))
    if src_uri.count('///') or src_uri.count('EXAMPLE'):
        test.cancel("The src_uri '%s' is invalid" % src_uri)

    dest_uri = libvirt_vm.complete_uri(params.get("migrate_dest_host",
                                                  "EXAMPLE"))
    if dest_uri.count('///') or dest_uri.count('EXAMPLE'):
        test.cancel("The dest_uri '%s' is invalid" % dest_uri)

    # Migrated vms' instance
    vms = env.get_all_vms()
    load_vm_names = params.get("load_vms").split()
    params['load_vms'] = vms

    cpu = int(params.get("smp", 1))
    memory = int(params.get("mem")) * 1024
    stress_tool = params.get("stress_tool", "")
    stress_type = params.get("migration_stress_type")
    require_stress_tool = "stress" in stress_tool
    vm_bytes = params.get("stress_vm_bytes")
    stress_args = params.get("stress_args")
    migration_type = params.get("migration_type")
    start_migration_vms = "yes" == params.get("start_migration_vms", "yes")
    thread_timeout = int(params.get("thread_timeout", 120))
    remote_host = params.get("migrate_dest_host")
    username = params.get("migrate_dest_user", "root")
    password = params.get("migrate_dest_pwd")
    prompt = params.get("shell_prompt", r"[\#\$]")

    # Set vm_bytes for start_cmd
    mem_total = utils_memory.memtotal()
    vm_reserved = len(vms) * memory
    if vm_bytes == "half":
        vm_bytes = (mem_total - vm_reserved) / 2
    elif vm_bytes == "shortage":
        vm_bytes = mem_total - vm_reserved + 524288
    if vm_bytes is not None:
        params["stress_args"] = stress_args % vm_bytes

    # Ensure stress tool is available in host
    if require_stress_tool and stress_type == "stress_on_host":
        utils_test.load_stress("stress_on_host", params)

    for vm in vms:
        # Keep vm dead for edit
        if vm.is_alive():
            vm.destroy()
        set_cpu_memory(vm.name, cpu, memory)

    try:
        vm_ipaddr = {}
        if start_migration_vms:
            for vm in vms:
                vm.start()
                session = vm.wait_for_login()
                vm_ipaddr[vm.name] = vm.get_address()

        # configure stress in VM
        if require_stress_tool and stress_type == "stress_in_vms":
            utils_test.load_stress("stress_in_vms", params, vms)

        do_stress_migration(vms, src_uri, dest_uri, stress_type,
                            migration_type, test, params, thread_timeout)
    finally:
        logging.debug("Cleanup vms...")
        for vm in vms:
            utils_test.libvirt.MigrationTest().cleanup_dest_vm(vm, None,
                                                               dest_uri)
            # Try to start vms in source once vms in destination are
            # cleaned up
            if not vm.is_alive():
                vm.start()
                vm.wait_for_login()
        utils_test.unload_stress(stress_type, vms)
