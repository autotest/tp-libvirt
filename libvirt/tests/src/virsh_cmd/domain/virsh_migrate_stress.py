import logging

from virttest import libvirt_vm
from virttest import utils_test
from virttest import utils_misc
from virttest import utils_package
from virttest import migration
from virttest import remote
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


def do_stress_migration(vms, srcuri, desturi, migration_type, test, params,
                        thread_timeout=60):
    """
    Migrate vms with stress.

    :param vms: migrated vms.
    :param srcuri: connect uri for source machine
    :param desturi: connect uri for destination machine
    :param migration_type: type of migration to be performed
    :param params: Test dict params
    :param thread_timeout: default timeout for migration thread

    :raise: test.fail if migration fails
    """
    migrate_setup = migration.MigrationTest()
    options = params.get("migrate_options")
    ping_count = int(params.get("ping_count", 10))
    migrate_times = 1
    migrate_back = params.get("virsh_migrate_back", "no") == "yes"
    if migrate_back:
        migrate_times = int(params.get("virsh_migrate_there_and_back", 1))
    uptime = {}

    for vm in vms:
        uptime[vm.name] = vm.uptime()
        logging.info("uptime of VM %s: %s", vm.name, uptime[vm.name])
        migrate_setup.ping_vm(vm, params, ping_count=ping_count)
    logging.debug("Starting migration...")
    migrate_options = ("%s --timeout %s"
                       % (options, params.get("virsh_migrate_timeout", 60)))
    for each_time in range(migrate_times):
        logging.debug("Migrating vms from %s to %s for %s time", srcuri,
                      desturi, each_time + 1)
        try:
            migrate_setup.do_migration(vms, srcuri, desturi, migration_type,
                                       options=migrate_options,
                                       thread_timeout=thread_timeout)
        except Exception as info:
            test.fail(info)

        uptime = migrate_setup.post_migration_check(vms, params, uptime,
                                                    uri=desturi)
        if migrate_back and "cross" not in migration_type:
            migrate_setup.migrate_pre_setup(srcuri, params)
            logging.debug("Migrating back to source from %s to %s for %s time",
                          desturi, srcuri, each_time + 1)
            try:
                migrate_setup.do_migration(vms, desturi, srcuri, migration_type,
                                           options=migrate_options,
                                           thread_timeout=thread_timeout,
                                           virsh_uri=desturi)
            except Exception as info:
                test.fail(info)
            uptime = migrate_setup.post_migration_check(vms, params, uptime)
            migrate_setup.migrate_pre_setup(srcuri, params, cleanup=True)


def run(test, params, env):
    """
    Test migration under stress.
    """
    vm_names = params.get("vms").split()
    if len(vm_names) < 2:
        test.cancel("Provide enough vms for migration")

    src_uri = "qemu:///system"
    dest_uri = libvirt_vm.complete_uri(params.get("migrate_dest_host",
                                                  "EXAMPLE"))
    if dest_uri.count('///') or dest_uri.count('EXAMPLE'):
        test.cancel("The dest_uri '%s' is invalid" % dest_uri)

    # Migrated vms' instance
    vms = env.get_all_vms()
    params["load_vms"] = list(vms)

    cpu = int(params.get("smp", 1))
    memory = int(params.get("mem")) * 1024
    stress_tool = params.get("stress_tool", "")
    remote_stress = params.get("migration_stress_remote", "no") == "yes"
    host_stress = params.get("migration_stress_host", "no") == "yes"
    vms_stress = params.get("migration_stress_vms", "no") == "yes"
    vm_bytes = params.get("stress_vm_bytes", "128M")
    stress_args = params.get("%s_args" % stress_tool)
    migration_type = params.get("migration_type")
    start_migration_vms = params.get("start_migration_vms", "yes") == "yes"
    thread_timeout = int(params.get("thread_timeout", 120))
    ubuntu_dep = ['build-essential', 'git']
    hstress = rstress = None
    vstress = {}

    # Set vm_bytes for start_cmd
    mem_total = utils_memory.memtotal()
    vm_reserved = len(vms) * memory
    if vm_bytes == "half":
        vm_bytes = (mem_total - vm_reserved) / 2
    elif vm_bytes == "shortage":
        vm_bytes = mem_total - vm_reserved + 524288
    if "vm-bytes" in stress_args:
        params["%s_args" % stress_tool] = stress_args % vm_bytes

    # Ensure stress tool is available in host
    if host_stress:
        # remove package manager installed tool to avoid conflict
        if not utils_package.package_remove(stress_tool):
            logging.error("Existing %s is not removed")
        if "stress-ng" in stress_tool and 'Ubuntu' in utils_misc.get_distro():
            params['stress-ng_dependency_packages_list'] = ubuntu_dep
        try:
            hstress = utils_test.HostStress(stress_tool, params)
            hstress.load_stress_tool()
        except utils_test.StressError as info:
            test.error(info)

    if remote_stress:
        try:
            server_ip = params['remote_ip']
            server_pwd = params['remote_pwd']
            server_user = params.get('remote_user', 'root')
            remote_session = remote.wait_for_login('ssh', server_ip, '22', server_user,
                                                   server_pwd, r"[\#\$]\s*$")
            # remove package manager installed tool to avoid conflict
            if not utils_package.package_remove(stress_tool, session=remote_session):
                logging.error("Existing %s is not removed")
            if("stess-ng" in stress_tool and
               'Ubuntu' in utils_misc.get_distro(session=remote_session)):
                params['stress-ng_dependency_packages_list'] = ubuntu_dep

            rstress = utils_test.HostStress(stress_tool, params, remote_server=True)
            rstress.load_stress_tool()
            remote_session.close()
        except utils_test.StressError as info:
            remote_session.close()
            test.error(info)

    for vm in vms:
        # Keep vm dead for edit
        if vm.is_alive():
            vm.destroy()
        set_cpu_memory(vm.name, cpu, memory)

    try:
        if start_migration_vms:
            for vm in vms:
                vm.start()
                session = vm.wait_for_login()
                # remove package manager installed tool to avoid conflict
                if not utils_package.package_remove(stress_tool, session=session):
                    logging.error("Existing %s is not removed")
                # configure stress in VM
                if vms_stress:
                    if("stress-ng" in stress_tool and
                       'Ubuntu' in utils_misc.get_distro(session=session)):
                        params['stress-ng_dependency_packages_list'] = ubuntu_dep
                    try:
                        vstress[vm.name] = utils_test.VMStress(vm, stress_tool, params)
                        vstress[vm.name].load_stress_tool()
                    except utils_test.StressError as info:
                        session.close()
                        test.error(info)
                session.close()

        do_stress_migration(vms, src_uri, dest_uri, migration_type, test,
                            params, thread_timeout)
    finally:
        logging.debug("Cleanup vms...")
        for vm in vms:
            migration.MigrationTest().cleanup_dest_vm(vm, None, dest_uri)
            # Try to start vms in source once vms in destination are
            # cleaned up
            if not vm.is_alive():
                vm.start()
                vm.wait_for_login()
            try:
                if vstress[vm.name]:
                    vstress[vm.name].unload_stress()
            except KeyError:
                continue

        if rstress:
            rstress.unload_stress()

        if hstress:
            hstress.unload_stress()
