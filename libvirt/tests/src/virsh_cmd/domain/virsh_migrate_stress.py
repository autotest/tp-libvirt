import logging
import time
from autotest.client.shared import error, utils_memory
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


def check_dest_vm_network(vm, remote_host, username, password,
                          shell_prompt):
    """
    Ping migrated vms on remote host.
    """
    session = remote.remote_login("ssh", remote_host, 22, username,
                                  password, shell_prompt)
    # Timeout to wait vm's network
    logging.debug("Getting vm's IP...")
    timeout = 60
    while timeout > 0:
        try:
            ping_cmd = "ping -c 4 %s" % vm.get_address()
            break
        except virt_vm.VMAddressError:
            time.sleep(5)
            timeout -= 5
    if timeout <= 0:
        raise error.TestFail("Can not get remote vm's IP.")
    status, output = session.cmd_status_output(ping_cmd)
    if status:
        raise error.TestFail("Check %s IP failed:%s" % (vm.name, output))


def do_stress_migration(vms, srcuri, desturi, stress_type,
                        migration_type, params, thread_timeout=60):
    """
    Migrate vms with stress.

    :param vms: migrated vms.
    """
    fail_info = utils_test.load_stress(stress_type, vms, params)
    if len(fail_info):
        logging.warning("Add stress for migration failed:%s", fail_info)

    migtest = utlv.MigrationTest()
    migtest.do_migration(vms, srcuri, desturi, migration_type, thread_timeout)

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
    stress_start_cmd = params.get("stress_start_cmd")
    migration_type = params.get("migration_type")
    start_migration_vms = "yes" == params.get("start_migration_vms", "yes")
    thread_timeout = int(params.get("thread_timeout", 120))
    remote_host = params.get("remote_ip")
    username = params.get("remote_user", "root")
    password = params.get("remote_pwd")
    prompt = params.get("shell_prompt", r"[\#\$]")

    # Set vm_bytes for start_cmd
    mem_total = utils_memory.memtotal()
    vm_reserved = len(vms) * memory
    if vm_bytes == "half":
        vm_bytes = (mem_total - vm_reserved) / 2
    elif vm_bytes == "shortage":
        vm_bytes = mem_total - vm_reserved + 524288
    if vm_bytes is not None:
        params["stress_start_cmd"] = stress_start_cmd % vm_bytes

    for vm in vms:
        # Keep vm dead for edit
        if vm.is_alive():
            vm.destroy()
        set_cpu_memory(vm.name, cpu, memory)

    try:
        if start_migration_vms:
            for vm in vms:
                vm.start()
                vm.wait_for_login()
                # TODO: recover vm if start failed?
        # TODO: set ssh-autologin automatically
        do_stress_migration(vms, src_uri, dest_uri, stress_type,
                            migration_type, params, thread_timeout)
        # Check network of vms on destination
        for vm in vms:
            check_dest_vm_network(vm, remote_host, username, password, prompt)
    finally:
        for vm in vms:
            utlv.MigrationTest().cleanup_dest_vm(vm, None, dest_uri)
            if vm.is_alive():
                vm.destroy()
