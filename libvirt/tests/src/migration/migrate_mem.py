import os
import platform

from avocado.utils import process

from virttest import libvirt_vm
from virttest import migration
from virttest import virsh
from virttest import utils_conn
from virttest import utils_hotplug
from virttest import utils_misc
from virttest import remote
from virttest import virt_vm


from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_cpu
from virttest.utils_libvirt import libvirt_config
from virttest.utils_test import libvirt

from virttest.libvirt_xml.devices.memory import Memory


def setup_test_mem_balloon(vm, params, test):
    """
    Setup steps for memory balloon device

    :param vm:  VM object
    :param params: dict, test parameters
    :param test: test object
    """
    test.log.debug("Setup for testing balloon device")
    guest_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm.name)

    # Update memory balloon device to correct model
    membal_dict = {'membal_model': 'virtio',
                   'membal_stats_period': '10'}
    libvirt.update_memballoon_xml(guest_xml, membal_dict)
    # Change the disk of the vm
    libvirt.set_vm_disk(vm, params)


def setup_test_mem_device(vm, params, test):
    """
    Setup steps for memory device

    :param vm:  VM object
    :param params: dict, test parameters
    :param test: test object
    """
    test.log.debug("Setup for testing memory device")
    guest_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm.name)

    libvirt_cpu.add_cpu_settings(guest_xml, params)

    dimm_params = {k.replace('memdev_', ''): v
                   for k, v in params.items() if k.startswith('memdev_')}
    dimm_xml = utils_hotplug.create_mem_xml(**dimm_params)

    libvirt.add_vm_device(guest_xml, dimm_xml)
    # Change the disk of the vm
    libvirt.set_vm_disk(vm, params)


def setup_test_large_mem_vm(vm, params, test):
    """
    Setup steps for large memory test

    :param vm:  VM object
    :param params: dict, test parameters
    :param test: test object
    """
    test.log.debug("Setup for testing large memory vm")
    guest_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm.name)

    memory_value = int(params.get("memory_value"))
    memory_unit = params.get("memory_unit")
    guest_xml.set_memory(memory_value)
    guest_xml.set_memory_unit(memory_unit)
    guest_xml.sync()
    # Change the disk of the vm
    libvirt.set_vm_disk(vm, params)


def create_file_within_nvdimm_disk(vm_session, test_str):
    """
    Create a test file in the nvdimm file disk

    :param vm_session: VM session
    :param test_str: str to be written into the nvdimm file disk
    """
    # Create a file system on /dev/pmem0
    if any(platform.platform().find(ver) for ver in ('el8', 'el9')):
        vm_session.cmd('mkfs.xfs -f /dev/pmem0 -m reflink=0')
    else:
        vm_session.cmd('mkfs.xfs -f /dev/pmem0')

    vm_session.cmd('mount -o dax /dev/pmem0 /mnt')
    vm_session.cmd('echo \"%s\" >/mnt/nvdimm_test' % test_str)
    vm_session.cmd('umount /mnt')


def remove_devices(guest_xml, device_path, device_model, test):
    """
    Remove existing device xml from given guest vm xml

    :param guest_xml:  VM xml
    :param device_path: str, the path of the device to be removed
    :param device_model: str, the model of the device to be removed
    :param test: test object
    """
    for elem in guest_xml.xmltreefile.findall(device_path):
        if elem.get('model') == device_model:
            test.log.debug("Remove one %s device", device_model)
            guest_xml.xmltreefile.remove(elem)
    guest_xml.xmltreefile.write()


def setup_test_mem_nvdimm(vm, params, test):
    """
    Setup steps for nvdimm device

    :param vm:  VM object
    :param params: dict, test parameters
    :param test: test object
    """
    # Change the disk of the vm
    libvirt.set_vm_disk(vm, params)
    if vm.is_alive():
        vm.destroy(gracefully=False)

    nfs_mount_dir = params.get('nfs_mount_dir')
    nvdimm_file_size = params.get('nvdimm_file_size')
    process.run("truncate -s {} {}/nvdimm".format(nvdimm_file_size,
                                                  nfs_mount_dir), shell=True)
    vm_attrs = eval(params.get('vm_attrs', '{}'))
    guest_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm.name)
    # At first remove all existing memory devices
    remove_devices(guest_xml, '/devices/memory', 'nvdimm', test)
    guest_xml.setup_attrs(**vm_attrs)
    mem_device_attrs = eval(params.get('mem_device_attrs', '{}'))
    dimm_device = Memory()
    dimm_device.setup_attrs(**mem_device_attrs)
    guest_xml.add_device(dimm_device)
    guest_xml.sync()

    vm.start()
    vm_session = vm.wait_for_serial_login(timeout=240)
    create_file_within_nvdimm_disk(vm_session, params.get("test_file_content"))
    vm_session.close()


def verify_test_default(vm, params, test):
    """
    Verify steps by default

    :param vm:  VM object
    :param params: dict, test parameters
    :param test: test object
    """
    check_str_local_log = params.get("check_str_local_log")
    if check_str_local_log:
        libvirt.check_logfile(check_str_local_log,
                              params.get("libvirtd_debug_file"),
                              eval(params.get('str_in_log', 'True')))


def verify_test_mem_balloon(vm, params, test):
    """
    Verify steps for memory balloon device

    :param vm:  VM object
    :param params: dict, test parameters
    :param test: test object
    """
    remote_ip = params.get("remote_ip")
    remote_user = params.get("remote_user")
    remote_pwd = params.get("remote_pwd")
    remote_virsh_dargs = {'remote_ip': remote_ip, 'remote_user': remote_user,
                          'remote_pwd': remote_pwd, 'unprivileged_user': None,
                          'ssh_remote_auth': True}
    ballooned_mem = params.get("ballooned_mem")
    vm_name = params.get("migrate_main_vm")
    virsh_args = {"debug": True}

    remote_virsh_session = virsh.VirshPersistent(**remote_virsh_dargs)
    remote_virsh_session.setmem(vm_name, ballooned_mem, None, None,
                                False, "", **virsh_args)

    def check_mem_balloon():
        """Check if memory balloon worked"""

        memstat_ouput = remote_virsh_session.dommemstat(vm_name, "",
                                                        **virsh_args)
        memstat_after = memstat_ouput.stdout_text
        mem_after = memstat_after.splitlines()[0].split()[1]
        if mem_after != ballooned_mem:
            test.log.debug("Current memory size is: %s", mem_after)
            return False
        return True

    check_ret = utils_misc.wait_for(check_mem_balloon, timeout=60)
    if not check_ret:
        test.fail("Memory is not ballooned to the expected size: %s"
                  % ballooned_mem)

    remote_virsh_session.close_session()


def verify_test_mem_device(vm, params, test):
    """
    Verify steps for memory device

    :param vm:  VM object
    :param params: dict, test parameters
    :param test: test object
    """
    verify_test_default(vm, params, test)
    remote_ip = params.get("remote_ip")
    remote_user = params.get("remote_user")
    remote_pwd = params.get("remote_pwd")
    # Create a remote runner for later use
    runner_on_target = remote.RemoteRunner(host=remote_ip,
                                           username=remote_user,
                                           password=remote_pwd)

    qemu_checks = params.get('qemu_checks', '').split('`')
    test.log.debug("qemu_checks:%s" % qemu_checks[0])
    for qemu_check in qemu_checks:
        libvirt.check_qemu_cmd_line(qemu_check, False, params,
                                    runner_on_target)


def verify_test_mem_nvdimm(vm, params, test):
    """
    Verify steps for memory balloon device

    :param vm:  VM object
    :param params: dict, test parameters
    :param test: test object
    """
    vm.connect_uri = params.get("virsh_migrate_desturi")
    expected_content = params.get("test_file_content")

    vm_session = vm.wait_for_serial_login(timeout=240)
    cmd = 'mount -o dax /dev/pmem0 /mnt'
    vm_session.cmd(cmd)
    cmd = "cat /mnt/nvdimm_test"
    file_content = vm_session.cmd_output(cmd)
    if expected_content not in file_content:
        test.fail("The expected content '{}' is not "
                  "found in target file \n{}".format(expected_content,
                                                     file_content))

    vm_session.cmd("echo '{}.back' > /mnt/nvdimm_test_back".format(expected_content))
    vm_session.cmd('umount /mnt')


def verify_test_back_default(vm, params, test):
    """
    Verify steps for migration back by default

    :param vm:  VM object
    :param params: dict, test parameters
    :param test: test object
    """
    pass


def verify_test_back_mem_nvdimm(vm, params, test):
    """
    Verify steps for migration back with nvdimm device

    :param vm:  VM object
    :param params: dict, test parameters
    :param test: test object
    """
    vm.connect_uri = params.get("virsh_migrate_connect_uri")
    if vm.serial_console is not None:
        test.log.debug("clean up old serial console")
        vm.cleanup_serial_console()
    vm.create_serial_console()
    vm_session = vm.wait_for_serial_login(timeout=240)
    cmd = 'mount -o dax /dev/pmem0 /mnt'
    vm_session.cmd(cmd)
    expected_content = params.get('test_file_content') + '.back'
    file_content = vm_session.cmd_output("cat /mnt/nvdimm_test_back")
    if expected_content not in file_content:
        test.fail("The expected content '{}' is not"
                  " found in target file \n{}".format(expected_content,
                                                      file_content))
    status, _ = vm_session.cmd_status_output("fdisk -l | grep 'Disk /dev/pmem0'")
    if status:
        test.fail("The disk '/dev/pmem0' is not found in 'fdisk -l' output")
    guest_xml = vm_xml.VMXML.new_from_dumpxml(vm.name)
    test.log.debug(guest_xml)
    found = False
    for mem_device in guest_xml.devices.by_device_tag('memory'):
        if mem_device.fetch_attrs()['mem_model'] == 'nvdimm':
            found = True
    if not found:
        test.fail('nvdimm memory device is not found in guest xml')


def cleanup_test_default(vm, params, test):
    """
    Cleanup steps by default

    :param vm:  VM object
    :param params: dict, test parameters
    :param test: test object
    """
    test.log.debug("Recover test environment")
    # Clean VM on destination and source
    try:
        migration_test = migration.MigrationTest()
        dest_uri = params.get("virsh_migrate_desturi")

        migration_test.cleanup_dest_vm(vm, vm.connect_uri, dest_uri)
        if vm.is_alive():
            vm.destroy(gracefully=False)
    except Exception as err:
        test.log.error(err)


def cleanup_test_mem_nvdimm(vm, params, test):
    """
    Cleanup steps for nvdimm device

    :param vm:  VM object
    :param params: dict, test parameters
    :param test: test object
    """
    cleanup_test_default(vm, params, test)
    test.log.debug("Recover test environment for memory nvdimm device test")
    os.remove(params.get('nfs_mount_src') + '/nvdimm')


def run_migration_back(params, test):
    """
    Execute migration back from target host to source host

    :param params: dict, test parameters
    :param test: test object
    """
    migrate_vm_back = "yes" == params.get("migrate_vm_back", "no")
    vm_name = params.get("migrate_main_vm")
    options = params.get("virsh_migrate_options", "--live --verbose")
    if migrate_vm_back:
        ssh_connection = utils_conn.SSHConnection(server_ip=params.get("client_ip"),
                                                  server_pwd=params.get("local_pwd"),
                                                  client_ip=params.get("server_ip"),
                                                  client_pwd=params.get("server_pwd"))
        try:
            ssh_connection.conn_check()
        except utils_conn.ConnectionError:
            ssh_connection.conn_setup()
            ssh_connection.conn_check()

        # Pre migration setup for local machine
        src_full_uri = libvirt_vm.complete_uri(
            params.get("migrate_source_host"))

        migration_test = migration.MigrationTest()
        migration_test.migrate_pre_setup(src_full_uri, params)
        cmd = "virsh migrate %s %s %s" % (vm_name, options,
                                          src_full_uri)
        test.log.debug("Start migration: %s", cmd)
        runner_on_target = remote.RemoteRunner(host=params.get("remote_ip"),
                                               username=params.get("remote_user"),
                                               password=params.get("remote_pwd"))
        cmd_result = remote.run_remote_cmd(cmd, params, runner_on_target)
        test.log.info(cmd_result)
        if cmd_result.exit_status:
            destroy_cmd = "virsh destroy %s" % vm_name
            remote.run_remote_cmd(destroy_cmd, params, runner_on_target,
                                  ignore_status=False)
            test.fail("Failed to run '%s' on remote: %s"
                      % (cmd, cmd_result))
    else:
        test.log.debug("No need to migrate back")


def run_migration(vm, params, test):
    """
    Execute migration from source host to target host

    :param vm: vm object
    :param params: dict, test parameters
    :param test: test object
    """
    migration_test = migration.MigrationTest()
    migration_test.check_parameters(params)

    params["disk_type"] = "file"
    virsh_options = params.get("virsh_options", "")
    options = params.get("virsh_migrate_options", "--live --verbose")
    func_params_exists = "yes" == params.get("func_params_exists", "yes")

    func_name = None
    mig_result = None

    # params for migration connection
    params["virsh_migrate_desturi"] = libvirt_vm.complete_uri(
        params.get("migrate_dest_host"))
    dest_uri = params.get("virsh_migrate_desturi")
    vm_name = params.get("migrate_main_vm")
    extra_args = {}

    if func_params_exists:
        extra_args.update({'func_params': params})

    remove_dict = {"do_search": '{"%s": "ssh:/"}' % dest_uri}
    src_libvirt_file = libvirt_config.remove_key_for_modular_daemon(
        remove_dict)

    try:
        if not vm.is_alive():
            vm.start()
    except virt_vm.VMStartError as e:
        test.fail("Failed to start VM %s: %s" % (vm_name, str(e)))

    test.log.debug("Guest xml after starting:\n%s",
                   vm_xml.VMXML.new_from_dumpxml(vm_name))

    # Check local guest network connection before migration
    vm.wait_for_login(restart_network=True).close()
    migration_test.ping_vm(vm, params)

    # Execute migration process
    vms = [vm]
    migration_test.do_migration(vms, None, dest_uri, 'orderly',
                                options, thread_timeout=900,
                                ignore_status=True, virsh_opt=virsh_options,
                                func=func_name, **extra_args)

    mig_result = migration_test.ret
    if int(mig_result.exit_status) == 0:
        migration_test.post_migration_check([vm], params, dest_uri=dest_uri)

    if src_libvirt_file:
        src_libvirt_file.restore()


def run(test, params, env):
    """
    Test migration with memory related configuration
    """
    case = params.get('case', '')
    # Get setup function
    setup_test = eval('setup_test_%s' % case)
    # Verify checkpoints
    verify_test = eval('verify_test_%s' % case) \
        if 'verify_test_%s' % case in globals() else verify_test_default
    # Verify checkpoints after migration back
    verify_test_back = eval('verify_test_back_%s' % case) \
        if 'verify_test_back_%s' % case in globals() else verify_test_back_default

    # Get cleanup function
    cleanup_test = eval('cleanup_test_%s' % case) \
        if 'cleanup_test_%s' % case in globals() else cleanup_test_default

    vm_name = params.get("migrate_main_vm")
    vm = env.get_vm(vm_name)
    # For safety reasons, we'd better back up  xmlfile.
    new_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    orig_config_xml = new_xml.copy()
    try:
        # Execute test
        setup_test(vm, params, test)
        run_migration(vm, params, test)
        verify_test(vm, params, test)
        run_migration_back(params, test)
        verify_test_back(vm, params, test)
    finally:
        cleanup_test(vm, params, test)
        test.log.info("Recovery VM XML configuration")
        orig_config_xml.sync()
