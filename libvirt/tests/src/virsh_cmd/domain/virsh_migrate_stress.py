import os
import logging

from avocado.core import exceptions

from virttest import ssh_key
from virttest import libvirt_vm
from virttest import utils_test
from virttest import data_dir
from virttest import nfs
from virttest.utils_test import libvirt as utlv
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
                        migration_type, params, thread_timeout=60):
    """
    Migrate vms with stress.

    :param vms: migrated vms.
    :param srcuri: connect uri for source machine
    :param desturi: connect uri for destination machine
    :param stress_type: type of stress test in VM
    :param migration_type: type of migration to be performed
    :param params: Test dict params
    :param thread_timeout: default timeout for migration thread

    :raise: exceptions.TestFail if migration fails
    """
    fail_info = utils_test.load_stress(stress_type, vms, params)

    migtest = utlv.MigrationTest()
    options = ''
    if migration_type == "compressed":
        options = "--compressed"
        migration_type = "orderly"
        shared_dir = os.path.dirname(data_dir.get_data_dir())
        src_file = os.path.join(shared_dir, "scripts", "duplicate_pages.py")
        dest_dir = "/tmp"
        for vm in vms:
            session = vm.wait_for_login()
            vm.copy_files_to(src_file, dest_dir)
            status = session.cmd_status("cd /tmp;python duplicate_pages.py")
            if status:
                fail_info.append("Set duplicated pages for vm failed.")

    if len(fail_info):
        logging.warning("Add stress for migration failed:%s", fail_info)

    logging.debug("Starting migration...")
    migrate_options = ("--live --unsafe %s --timeout %s"
                       % (options, params.get("virsh_migrate_timeout", 60)))
    migtest.do_migration(vms, srcuri, desturi, migration_type,
                         options=migrate_options,
                         thread_timeout=thread_timeout)

    # vms will be shutdown, so no need to do this cleanup
    # And migrated vms may be not login if the network is local lan
    if stress_type == "stress_on_host":
        utils_test.unload_stress(stress_type, vms)

    if not migtest.RET_MIGRATION:
        raise exceptions.TestFail()


def run(test, params, env):
    """
    Test migration under stress.
    """
    vm_names = params.get("migration_vms").split()
    if len(vm_names) < 2:
        raise exceptions.TestSkipError("Provide enough vms for migration")

    src_uri = libvirt_vm.complete_uri(params.get("migrate_source_host",
                                                 "EXAMPLE"))
    if src_uri.count('///') or src_uri.count('EXAMPLE'):
        raise exceptions.TestSkipError("The src_uri '%s' is invalid" % src_uri)

    dest_uri = libvirt_vm.complete_uri(params.get("migrate_dest_host",
                                                  "EXAMPLE"))
    if dest_uri.count('///') or dest_uri.count('EXAMPLE'):
        raise exceptions.TestSkipError("The dest_uri '%s' is invalid" %
                                       dest_uri)

    # Params for NFS and SSH setup
    params["server_ip"] = params.get("migrate_dest_host")
    params["server_user"] = "root"
    params["server_pwd"] = params.get("migrate_dest_pwd")
    params["client_ip"] = params.get("migrate_source_host")
    params["client_user"] = "root"
    params["client_pwd"] = params.get("migrate_source_pwd")
    params["nfs_client_ip"] = params.get("migrate_dest_host")
    params["nfs_server_ip"] = params.get("migrate_source_host")

    # Configure NFS client on remote host
    nfs_client = nfs.NFSClient(params)
    nfs_client.setup()

    # Migrated vms' instance
    vms = []
    for vm_name in vm_names:
        vms.append(libvirt_vm.VM(vm_name, params, test.bindir,
                                 env.get("address_cache")))

    load_vm_names = params.get("load_vms").split()
    # vms for load
    load_vms = []
    for vm_name in load_vm_names:
        load_vms.append(libvirt_vm.VM(vm_name, params, test.bindir,
                                      env.get("address_cache")))
    params['load_vms'] = load_vms

    cpu = int(params.get("smp", 1))
    memory = int(params.get("mem")) * 1024
    stress_type = params.get("migration_stress_type")
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
                vm.wait_for_login()
                vm_ipaddr[vm.name] = vm.get_address()
                # TODO: recover vm if start failed?
        # Config ssh autologin for remote host
        ssh_key.setup_ssh_key(remote_host, username, password, port=22)

        do_stress_migration(vms, src_uri, dest_uri, stress_type,
                            migration_type, params, thread_timeout)
        # Check network of vms on destination
        if start_migration_vms and migration_type != "cross":
            for vm in vms:
                utils_test.check_dest_vm_network(vm, vm_ipaddr[vm.name],
                                                 remote_host,
                                                 username, password, prompt)
    finally:
        logging.debug("Cleanup vms...")
        for vm_name in vm_names:
            vm = libvirt_vm.VM(vm_name, params, test.bindir,
                               env.get("address_cache"))
            utlv.MigrationTest().cleanup_dest_vm(vm, None, dest_uri)
            if vm.is_alive():
                vm.destroy(gracefully=False)

        if nfs_client:
            logging.info("Cleanup NFS client environment...")
            nfs_client.cleanup()
        env.clean_objects()
