import os

from avocado.utils import process
from virttest import libvirt_vm
from virttest import migration
from virttest import virsh
from virttest import remote
from virttest import virt_vm
from virttest import data_dir
from virttest import xml_utils

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_config
from virttest.utils_test import libvirt

from virttest.libvirt_xml.devices.channel import Channel


def setup_test_pty_channel(vm, params, test):
    """
    Setup steps for virtio pty channel device

    :param vm:  VM object
    :param params: dict, test parameters
    :param test: test object
    """
    test.log.debug("Setup for testing pty channel device")
    guest_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm.name)
    added_channel = Channel()
    channel_attrs = eval(params.get("channel_attrs", '{}'))
    added_channel.setup_attrs(**channel_attrs)
    guest_xml.add_device(added_channel)
    guest_xml.sync()
    # Change the disk of the vm
    libvirt.set_vm_disk(vm, params)


def verify_test_default(vm, params, test):
    """
    Verify steps by default

    :param vm:  VM object
    :param params: dict, test parameters
    :param test: test object
    """
    check_str_local_log = params.get("check_str_local_log")
    if check_str_local_log:
        libvirt.check_logfile(check_str_local_log, params.get("libvirtd_debug_file"))


def get_vm_ip_on_remote(vm, params):
    """
    Get the vm ip on remote host after migration

    :param vm:  VM object
    :param params: dict, test parameters
    :return: str, the vm IP
    """
    remote_session = remote.wait_for_login('ssh', params.get('server_ip'), '22',
                                           params.get('server_user'), params.get('server_pwd'),
                                           r"[\#\$]\s*$")
    vm_ip = vm.get_address(session=remote_session, timeout=480)
    remote_session.close()
    return vm_ip


def send_msg_from_guest_to_host(params, test, host_source, runner_on_target, remote_vm_obj, channel_dev_in_guest):
    """
    Send message from the guest and check them from host

    :param params: dict, test parameters
    :param test: test object
    :param host_source: host file to receive channel message
    :param runner_on_target: runner on the remote host
    :param remote_vm_obj: remote.VMManager object
    :param channel_dev_in_guest: device within the guest
    """
    # Prepare to wait for message on remote host from the channel.
    # Run like 'cat /dev/pts/6 &' on remote host
    remote.run_remote_cmd(params.get('cmd_check_msg_from_host') % host_source, params,
                          remote_runner=runner_on_target, ignore_status=False)

    # Send message from remote guest to the channel file
    remote_vm_obj.run_command(params.get('cmd_send_msg_from_guest') % channel_dev_in_guest)
    test.log.debug("Message '%s' was sent from guest to host", params.get('msg_sent_by_guest'))

    # Check message on remote host from the channel
    remote.run_remote_cmd(params.get('cmd_receive_msg_from_host'), params,
                          remote_runner=runner_on_target, ignore_status=False)
    test.log.debug("Message '%s' was received from host", params.get('msg_sent_by_guest'))


def send_msg_from_host_to_guest(params, test, host_source, runner_on_target, remote_vm_obj, channel_dev_in_guest):
    """
    Send message from the host and check them from guest

    :param params: dict, test parameters
    :param test: test object
    :param host_source: host file to send channel message
    :param runner_on_target: runner on the remote host
    :param remote_vm_obj: remote.VMManager object
    :param channel_dev_in_guest: device within the guest
    """
    remote_vm_obj.run_command(params.get('cmd_check_msg_from_guest') % channel_dev_in_guest)

    remote.run_remote_cmd(params.get('cmd_send_msg_from_host') % host_source, params,
                          remote_runner=runner_on_target, ignore_status=False)
    test.log.debug("Message '%s' was sent from host to guest", params.get('msg_sent_by_host'))

    remote_vm_obj.run_command(params.get('cmd_receive_msg_from_guest'))
    test.log.debug("Message '%s' was received from guest", params.get('msg_sent_by_host'))


def verify_test_pty_channel(vm, params, test):
    """
    Verify steps for pty channel device

    :param vm:  VM object
    :param params: dict, test parameters
    :param test: test object
    """
    # Get the channel device source path of remote guest
    server_ip = params.get('server_ip')
    server_user = params.get('server_user')
    server_pwd =  params.get('server_pwd')
    remote_virsh_dargs = {'remote_ip': server_ip, 'remote_user': server_user,
                          'remote_pwd':server_pwd, 'unprivileged_user': None,
                          'ssh_remote_auth': True}
    remote_virsh_session = virsh.VirshPersistent(**remote_virsh_dargs)
    file_path = os.path.join(data_dir.get_tmp_dir(), '%s_remote_dumpxml' % vm.name)
    remote_virsh_session.dumpxml(vm.name, to_file=file_path,
                                 debug=True,
                                 ignore_status=True)
    local_vmxml = vm_xml.VMXML.new_from_dumpxml(vm.name)
    local_vmxml.xmltreefile = xml_utils.XMLTreeFile(file_path)
    host_source = ''
    channel_type = params.get('channel_type')
    for elem in local_vmxml.devices.by_device_tag('channel'):
        test.log.debug("Found channel device {}".format(elem))
        if elem.type_name == channel_type:
            host_source = elem.source.get('path')
            test.log.debug("Remote guest uses {} for channel device".format(host_source))
            break
    remote_virsh_session.close_session()
    if not host_source:
        test.fail("Can not find source for %s channel on remote host" % channel_type)
    runner_on_target = remote.RemoteRunner(host=server_ip,
                                           username=server_user,
                                           password=server_pwd)

    params.update({'vm_ip': get_vm_ip_on_remote(vm, params), 'vm_pwd': params.get("password")})
    remote_vm_obj = remote.VMManager(params)
    remote_vm_obj.setup_ssh_auth()
    channel_dev_in_guest = remote_vm_obj.run_command(params.get('cmd_get_channel_dev_in_guest')).stdout_text.strip()
    send_msg_from_guest_to_host(params, test, host_source, runner_on_target, remote_vm_obj, channel_dev_in_guest)
    send_msg_from_host_to_guest(params, test, host_source, runner_on_target, remote_vm_obj, channel_dev_in_guest)


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
        migration_test.post_migration_check([vm], params, uri=dest_uri)

    if src_libvirt_file:
        src_libvirt_file.restore()


def run(test, params, env):
    """
    Test migration with console or serial device related configuration
    """
    case = params.get('case', '')
    # Get setup function
    setup_test = eval('setup_test_%s' % case)
    # Verify checkpoints
    verify_test = eval('verify_test_%s' % case) \
        if 'verify_test_%s' % case in globals() else verify_test_default
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
    finally:
        cleanup_test(vm, params, test)
        test.log.info("Recovery VM XML configuration")
        orig_config_xml.sync()
