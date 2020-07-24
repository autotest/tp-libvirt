import logging
import time
import threading

from avocado.utils import process
from avocado.core import exceptions

from virttest import utils_test
from virttest import libvirt_vm
from virttest import virsh
from virttest import ssh_key
from virttest import migration
from virttest import libvirt_version

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt as utlv


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


def copied_migration(test, vm, params, blockjob_type=None, block_target="vda"):
    """
    Migrate vms with storage copied under some stress.
    And during it, some qemu-monitor-command will be sent.
    """
    dest_uri = params.get("migrate_dest_uri")
    remote_host = params.get("migrate_dest_host")
    copy_option = params.get("copy_storage_option", "")
    username = params.get("remote_user")
    password = params.get("migrate_dest_pwd")
    timeout = int(params.get("thread_timeout", 1200))
    options = "--live %s --unsafe" % copy_option

    # Get vm ip for remote checking
    if vm.is_dead():
        vm.start()
    vm.wait_for_login()
    vms_ip = {}
    vms_ip[vm.name] = vm.get_address()
    logging.debug("VM %s IP: %s", vm.name, vms_ip[vm.name])

    # Start to load stress
    stress_type = params.get("migrate_stress_type")
    if stress_type == "cpu":
        params['stress_args'] = "--cpu 2 --quiet --timeout 60"
    elif stress_type == "memory":
        params['stress_args'] = "--vm 2 --vm-bytes 256M --vm-keep --timeout 60"
    if stress_type is not None:
        utils_test.load_stress("stress_in_vms", params=params, vms=[vm])

    cp_mig = migration.MigrationTest()
    migration_thread = threading.Thread(target=cp_mig.thread_func_migration,
                                        args=(vm, dest_uri, options))
    migration_thread.start()
    # Wait for migration launched
    time.sleep(5)
    job_ret = virsh.domjobinfo(vm.name, debug=True)
    if job_ret.exit_status:
        test.error("Prepare migration for blockjob failed.")

    # Execute some qemu monitor commands
    pause_cmd = "block-job-pause %s" % block_target
    resume_cmd = "block-job-resume %s" % block_target
    cancel_cmd = "block-job-cancel %s" % block_target
    complete_cmd = "block-job-complete %s" % block_target

    blockjob_failures = []
    try:
        if blockjob_type == "cancel":
            virsh.qemu_monitor_command(vm.name, cancel_cmd, debug=True,
                                       ignore_status=False)
        elif blockjob_type == "pause_resume":
            virsh.qemu_monitor_command(vm.name, pause_cmd, debug=True,
                                       ignore_status=False)
            # TODO: Check whether it is paused.
            virsh.qemu_monitor_command(vm.name, resume_cmd, debug=True,
                                       ignore_status=False)
        elif blockjob_type == "complete":
            virsh.qemu_monitor_command(vm.name, complete_cmd, debug=True,
                                       ignore_status=False)
    except process.CmdError as detail:
        blockjob_failures.append(str(detail))

    # Job info FYI
    virsh.domjobinfo(vm.name, debug=True)

    if len(blockjob_failures):
        timeout = 30

    migration_thread.join(timeout)
    if migration_thread.isAlive():
        logging.error("Migrate %s timeout.", migration_thread)
        cp_mig.RET_LOCK.acquire()
        cp_mig.RET_MIGRATION = False
        cp_mig.RET_LOCK.release()

    if len(blockjob_failures):
        cp_mig.cleanup_dest_vm(vm, None, dest_uri)
        test.fail("Run qemu monitor command failed %s"
                  % blockjob_failures)

    check_ip_failures = []
    if cp_mig.RET_MIGRATION:
        try:
            utils_test.check_dest_vm_network(vm, vms_ip[vm.name],
                                             remote_host, username,
                                             password)
        except exceptions.TestFail as detail:
            check_ip_failures.append(str(detail))
        cp_mig.cleanup_dest_vm(vm, None, dest_uri)
        if blockjob_type in ["cancel", "complete"]:
            test.fail("Storage migration passed even after "
                      "cancellation.")
    else:
        cp_mig.cleanup_dest_vm(vm, None, dest_uri)
        if blockjob_type in ["cancel", "complete"]:
            logging.error("Expected Migration Error for %s", blockjob_type)
            return
        else:
            test.fail("Command blockjob does not work well under "
                      "storage copied migration.")

    if len(check_ip_failures):
        test.fail("Check IP failed:%s" % check_ip_failures)


def run(test, params, env):
    """
    Test qemu-monitor-command blockjobs by migrating with option
    --copy-storage-all or --copy-storage-inc.
    """
    if not libvirt_version.version_compare(1, 0, 1):
        test.cancel("Blockjob functions - "
                    "complete,pause,resume are"
                    "not supported in current libvirt version.")

    vm = env.get_vm(params.get("main_vm"))
    cpu_size = int(params.get("cpu_size", "1"))
    memory_size = int(params.get("memory_size", "1048576"))
    primary_target = vm.get_first_disk_devices()["target"]
    file_path, file_size = vm.get_device_size(primary_target)
    # Convert to Gib
    file_size = int(file_size) // 1073741824
    image_format = utils_test.get_image_info(file_path)["format"]

    remote_host = params.get("migrate_dest_host", "REMOTE.EXAMPLE")
    remote_user = params.get("remote_user", "root")
    remote_passwd = params.get("migrate_dest_pwd", "PASSWORD.EXAMPLE")
    if remote_host.count("EXAMPLE"):
        test.cancel("Config remote or local host first.")
    # Config ssh autologin for it
    ssh_key.setup_ssh_key(remote_host, remote_user, remote_passwd, port=22)

    # Define a new vm with modified cpu/memory
    new_vm_name = "%s_blockjob" % vm.name
    if vm.is_alive():
        vm.destroy()
    utlv.define_new_vm(vm.name, new_vm_name)
    try:
        set_cpu_memory(new_vm_name, cpu_size, memory_size)
        vm = libvirt_vm.VM(new_vm_name, vm.params, vm.root_dir,
                           vm.address_cache)
    except Exception:   # Make sure created vm is cleaned up
        virsh.remove_domain(new_vm_name)
        raise

    rdm_params = {"remote_ip": remote_host, "remote_user": remote_user,
                  "remote_pwd": remote_passwd}
    rdm = utils_test.RemoteDiskManager(rdm_params)

    try:
        vm = libvirt_vm.VM(new_vm_name, vm.params, vm.root_dir,
                           vm.address_cache)
        vm.start()

        rdm.create_image("file", file_path, file_size, None, None,
                         img_frmt=image_format)

        logging.debug("Start migration...")
        copied_migration(test, vm, params, params.get("qmp_blockjob_type"),
                         primary_target)
    finally:
        # Recover created vm
        if vm.is_alive():
            vm.destroy()
        if vm.name == new_vm_name:
            vm.undefine()
        rdm.remove_path("file", file_path)
        rdm.runner.session.close()
