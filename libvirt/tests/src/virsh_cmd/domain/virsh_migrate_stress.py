import logging
import time
from autotest.client.shared import utils_memory
from autotest.client.shared import error
from autotest.client.shared import ssh_key
from virttest import libvirt_vm, virt_vm
from virttest import utils_test, remote
from virttest.utils_test import libvirt as utlv
from virttest.libvirt_xml import vm_xml


def set_cpu_memory(vm_name, cpu, memory):
    """
    Change vms' cpu and memory.
    """
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    vmxml.vcpu = cpu
    # To avoid exceeded current memory
    vmxml.max_mem = memory
    vmxml.current_mem = memory
    logging.debug("VMXML info:\n%s", vmxml.get('xml'))
    vmxml.undefine()
    vmxml.define()


def check_dest_vm_network(vm, ip, remote_host, username, password,
                          shell_prompt):
    """
    Ping migrated vms on remote host.
    """
    session = remote.remote_login("ssh", remote_host, 22, username,
                                  password, shell_prompt)
    # Timeout to wait vm's network
    logging.debug("verifying VM's IP...")
    timeout = 60
    ping_failed = True
    ping_cmd = "ping -c 4 %s" % ip
    while timeout > 0:
        ps, po = session.cmd_status_output(ping_cmd)
        if ps:
            time.sleep(5)
            timeout -= 5
            continue
        logging.error(po)
        ping_failed = False
        break
    if ping_failed:
        raise error.TestFail("Check %s IP failed." % vm.name)


def do_stress_migration(vms, srcuri, desturi, stress_type,
                        migration_type, params, thread_timeout=60):
    """
    Migrate vms with stress.

    :param vms: migrated vms.
    """
    fail_info = utils_test.load_stress(stress_type, vms, params)
    if len(fail_info):
        logging.warning("Add stress for migration failed:%s", fail_info)

    migrate_options = "--live --timeout %s" % params.get("virsh_migrate_timeout", 60)
    migtest = utlv.MigrationTest()
    migtest.do_migration(vms, srcuri, desturi, migration_type, options=migrate_options,
                         thread_timeout=thread_timeout)

    # vms will be shutdown, so no need to do this cleanup
    # And migrated vms may be not login if the network is local lan
    if stress_type == "stress_on_host":
        utils_test.unload_stress(stress_type, vms)

    if not migtest.RET_MIGRATION:
        raise error.TestFail()


def run(test, params, env):
    """
    Test migration under stress.
    """
    vm_names = params.get("migration_vms").split()
    if len(vm_names) < 2:
        raise error.TestNAError("Provide enough vms for migration first.")

    src_uri = params.get("migrate_src_uri", "qemu+ssh://EXAMPLE/system")
    if src_uri.count('///') or src_uri.count('EXAMPLE'):
        raise error.TestNAError("The src_uri '%s' is invalid", src_uri)

    dest_uri = params.get("migrate_dest_uri", "qemu+ssh://EXAMPLE/system")
    if dest_uri.count('///') or dest_uri.count('EXAMPLE'):
        raise error.TestNAError("The dest_uri '%s' is invalid", dest_uri)

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
                check_dest_vm_network(vm, vm_ipaddr[vm.name], remote_host,
                                      username, password, prompt)
    finally:
        logging.debug("Cleanup vms...")
        for vm_name in vm_names:
            vm = libvirt_vm.VM(vm_name, params, test.bindir,
                               env.get("address_cache"))
            utlv.MigrationTest().cleanup_dest_vm(vm, None, dest_uri)
            if vm.is_alive():
                vm.destroy(gracefully=False)
        env.clean_objects()
