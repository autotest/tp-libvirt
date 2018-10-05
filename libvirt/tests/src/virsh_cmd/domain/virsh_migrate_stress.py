import logging
import os
from fractions import gcd

from virttest import libvirt_vm
from virttest import utils_test
from virttest import utils_misc
from virttest import kernel_interface
from virttest import utils_package
from virttest import test_setup
from virttest import utils_numeric
from virttest.libvirt_xml import vm_xml
from virttest.staging import utils_memory

"""
Assert Anonpages for THP is being exercised or not
"""


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


def enable_thp(test, params, sysfs_path, value=None, session=None, name="host"):
    """
    Method to enable/disable THP on host/remote host/guest

    :param test: Test Object
    :param params: Test Dict params
    :param sysfs_path: path to be used to set THP
    :param value: value to be set
    :param session: ShellSession Object
    :param name: name used to backup initial value to restore in cleanup
    """
    thp = kernel_interface.SysFS(sysfs_path, session=session)
    if value:
        # backup to restore
        params["%s_%s" % (name, sysfs_path)] = thp.sys_fs_value
    else:
        value = params["%s_%s" % (name, sysfs_path)]
    if value not in thp.sys_fs_value:
        thp.sys_fs_value = value
        if value not in thp.sys_fs_value:
            test.error("Failed to set %s in %s" % (value, sysfs_path))


def post_migration_check(vms, uptime, test, params, uri=None):
    """
    Validating migration by performing checks in this method
    * uptime of the migrated vm > uptime of vm before migration
    * ping vm from target host
    * check vm state after migration

    :param vms: VM objects of migrating vms
    :param uptime: uptime dict of vms before migration
    :param uri: target virsh uri
    :return: updated dict of uptime
    """
    migrate_setup = utils_test.libvirt.MigrationTest()
    vm_state = params.get("virsh_migrated_state", "running")
    ping_count = int(params.get("ping_count", 10))
    for vm in vms:
        if uri:
            vm_uri = vm.connect_uri
            vm.connect_uri = uri
        vm_uptime = vm.uptime(connect_uri=uri)
        logging.info("uptime of migrated VM %s: %s", vm.name,
                     vm_uptime)
        if vm_uptime < uptime[vm.name]:
            test.fail("vm went for a reboot during migration")
        if not migrate_setup.check_vm_state(vm.name, vm_state, uri=uri):
            test.fail("Migrated VMs failed to be in %s state at "
                      "destination" % vm_state)
        logging.info("Guest state is '%s' at destination is as expected",
                     vm_state)
        migrate_setup.ping_vm(vm, test, params, uri=uri,
                              ping_count=ping_count)
        # update vm uptime to check when migrating back
        uptime[vm.name] = vm_uptime

        # revert the connect_uri to avoid cleanup errors
        if uri:
            vm.connect_uri = vm_uri
    return uptime


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
    migrate_setup = utils_test.libvirt.MigrationTest()
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
        migrate_setup.ping_vm(vm, test, params, ping_count=ping_count)
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
        except Exception, info:
            test.fail(info)

        uptime = post_migration_check(vms, uptime, test, params, uri=desturi)
        if migrate_back and "cross" not in migration_type:
            migrate_setup.migrate_pre_setup(srcuri, params)
            logging.debug("Migrating back to source from %s to %s for %s time",
                          desturi, srcuri, each_time + 1)
            for vm in vms:
                vm.connect_uri = desturi
            try:
                migrate_setup.do_migration(vms, desturi, srcuri, migration_type,
                                           options=migrate_options,
                                           thread_timeout=thread_timeout)
            except Exception, info:
                test.fail(info)
            finally:
                for vm in vms:
                    vm.connect_uri = srcuri
            uptime = post_migration_check(vms, uptime, test, params)
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

    cpu = int(params.get("set_smp", 1))
    memory = int(params.get("set_mem")) * 1024
    stress_tool = params.get("stress_tool", "")
    remote_stress = params.get("migration_stress_remote", "no") == "yes"
    host_stress = params.get("migration_stress_host", "no") == "yes"
    vms_stress = params.get("migration_stress_vms", "no") == "yes"
    vm_bytes = params.get("stress_vm_bytes")
    stress_args = params.get("%s_args" % stress_tool)
    migration_type = params.get("migration_type")
    start_migration_vms = params.get("start_migration_vms", "yes") == "yes"
    thread_timeout = int(params.get("thread_timeout", 120))
    thp_stress = params.get("transparent_hugepages", "no") == "yes"
    ubuntu_dep = ['build-essential', 'git']
    hstress = rstress = None
    vstress = {}
    params['server_ip'] = params['remote_ip']
    params['server_pwd'] = params['remote_pwd']
    params['server_user'] = params.get('remote_user', 'root')
    remote_session = test_setup.remote_session(params)

    mem_free = utils_memory.freememtotal()
    remote_mem_free = utils_memory.freememtotal(remote_session)

    if thp_stress and (host_stress or remote_stress):
        hp_size = utils_memory.get_huge_page_size()
        remote_hp_size = utils_memory.get_huge_page_size(remote_session)
        # align vm memory with hugepage size
        if remote_hp_size == hp_size:
            align = hp_size
        else:
            align = hp_size * remote_hp_size // gcd(hp_size, remote_hp_size)
        memory = utils_numeric.align_value(memory, align)

    vm_reserved = len(vms) * memory
    if mem_free < vm_reserved:
        test.cancel("Memory is not available to bring up VMs")

    if remote_mem_free < vm_reserved:
        test.cancel("Memory is not available in remote host to migrate VMs")

    # Set vm_bytes for start_cmd
    host_vm_bytes = (mem_free - vm_reserved) * 0.95
    remote_vm_bytes = (remote_mem_free - vm_reserved)
    if host_stress:
        if vm_bytes == "half":
            host_vm_bytes = (mem_free - vm_reserved) * 0.5
        elif vm_bytes == "shortage":
            host_vm_bytes = (mem_free - vm_reserved) * 1.5
        if thp_stress:
            host_vm_bytes = utils_numeric.align_value(host_vm_bytes, align)

    if remote_stress:
        if vm_bytes == "half":
            remote_vm_bytes = (remote_mem_free - vm_reserved) * 0.5
        elif vm_bytes == "shortage":
            remote_vm_bytes = (remote_mem_free - vm_reserved) * 1.5
        if thp_stress:
            remote_vm_bytes = utils_numeric.align_value(remote_vm_bytes, align)
    remote_session.close()

    # calculate vm bytes based on the hugepage size supported for THP to use
    if thp_stress:
        RH_THP_PATH = "/sys/kernel/mm/redhat_transparent_hugepage"
        if os.path.isdir(RH_THP_PATH):
            thp_path = RH_THP_PATH
        else:
            thp_path = "/sys/kernel/mm/transparent_hugepage"
        thp = os.path.join(thp_path, "enabled")
        defrag = os.path.join(thp_path, "defrag", "enabled")
        thp_option = params.get("thp_option")
        if remote_stress:
            remote_session = test_setup.remote_session(params)
            enable_thp(test, params, thp, thp_option, session=remote_session,
                       name="remote")
            enable_thp(test, params, defrag, thp_option, session=remote_session,
                       name="remote")
            remote_session.close()

        if vms_stress:
            for vm in vms:
                vm.start()
                session = vm.wait_for_login()
                enable_thp(test, params, thp, thp_option, session=session,
                           name=vm.name)
                enable_thp(test, params, defrag, thp_option, session=session,
                           name=vm.name)
                session.close()

        if host_stress:
            enable_thp(test, params, thp, thp_option)
            enable_thp(test, params, defrag, thp_option)

    # Ensure stress tool is available in host
    if host_stress:
        if "vm-bytes" in stress_args:
            params["%s_args" % stress_tool] = stress_args % host_vm_bytes
        # remove package manager installed tool to avoid conflict
        if not utils_package.package_remove(stress_tool):
            logging.error("Existing %s is not removed")
        if "stress-ng" in stress_tool and 'Ubuntu' in utils_misc.get_distro():
            params['stress-ng_dependency_packages_list'] = ubuntu_dep
        try:
            hstress = utils_test.HostStress(stress_tool, params)
            hstress.load_stress_tool()
        except utils_test.StressError, info:
            test.error(info)

    if remote_stress:
        if "vm-bytes" in stress_args:
            params["%s_args" % stress_tool] = stress_args % remote_vm_bytes
        try:
            remote_session = test_setup.remote_session(params)
            # remove package manager installed tool to avoid conflict
            if not utils_package.package_remove(stress_tool,
                                                session=remote_session):
                logging.error("Existing %s is not removed")
            if("stess-ng" in stress_tool and
               'Ubuntu' in utils_misc.get_distro(session=remote_session)):
                params['stress-ng_dependency_packages_list'] = ubuntu_dep

            rstress = utils_test.HostStress(stress_tool, params, remote_server=True)
            rstress.load_stress_tool()
            remote_session.close()
        except utils_test.StressError, info:
            remote_session.close()
            test.error(info)

    for vm in vms:
        # Keep vm dead for edit
        if vm.is_alive():
            vm.destroy()
        set_cpu_memory(vm.name, cpu, memory)

    try:
        if start_migration_vms:
            # configure stress in VM
            if vms_stress:
                for vm in vms:
                    vm.start()
                    session = vm.wait_for_login()
                    # handle for POWER8
                    vm_hp_size = utils_memory.get_huge_page_size(session)
                    vm_mem_free = utils_memory.freememtotal(session) * 0.95
                    if vm_bytes == "half":
                        guest_vm_bytes = mem_free * 0.5
                    elif vm_bytes == "shortage":
                        guest_vm_bytes = mem_free * 1.5
                    if thp_stress:
                        guest_vm_bytes = utils_numeric.align_value(guest_vm_bytes,
                                                                   vm_hp_size)
                    if "vm-bytes" in stress_args:
                        params["%s_args" % stress_tool] = stress_args % guest_vm_bytes

                    # remove package manager installed tool to avoid conflict
                    if not utils_package.package_remove(stress_tool, session=session):
                        logging.error("Existing %s is not removed")
                    if("stress-ng" in stress_tool and
                       'Ubuntu' in vm.get_distro()):
                        params['stress-ng_dependency_packages_list'] = ubuntu_dep
                    try:
                        vstress[vm.name] = utils_test.VMStress(vm, stress_tool, params)
                        vstress[vm.name].load_stress_tool()
                    except utils_test.StressError, info:
                        session.close()
                        test.error(info)
                    session.close()

        do_stress_migration(vms, src_uri, dest_uri, migration_type, test,
                            params, thread_timeout)
    finally:
        logging.debug("Cleanup vms...")
        params["connect_uri"] = src_uri
        for vm in vms:
            utils_test.libvirt.MigrationTest().cleanup_dest_vm(vm, None,
                                                               dest_uri)
            # Try to start vms in source once vms in destination are
            # cleaned up
            if not vm.is_alive():
                vm.start()
            session = vm.wait_for_login()
            try:
                if vstress[vm.name]:
                    vstress[vm.name].unload_stress()
                    if thp_stress:
                        enable_thp(test, params, thp, session=session, name=vm.name)
                        enable_thp(test, params, defrag, session=session, name=vm.name)
                    session.close()
            except KeyError:
                continue

        if rstress:
            rstress.unload_stress()
            if thp_stress:
                remote_session = test_setup.remote_session(params)
                enable_thp(test, params, thp, session=remote_session, name="remote")
                enable_thp(test, params, defrag, session=remote_session, name="remote")
                remote_session.close()

        if hstress:
            hstress.unload_stress()
            if thp_stress:
                enable_thp(test, params, thp)
                enable_thp(test, params, defrag)
