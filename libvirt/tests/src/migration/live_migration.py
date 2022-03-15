import logging as log

from avocado.utils import process

from virttest import libvirt_vm
from virttest import migration
from virttest import remote as remote_old
from virttest import virsh
from virttest import libvirt_remote
from virttest import libvirt_version
from virttest import utils_net

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_config
from virttest.utils_libvirt import libvirt_disk

from provider.migration import migration_base


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def get_used_port(func_returns):
    """
    Get the port used in migration

    :param func_returns: dict, the function return results
                               from MigrationTest
    :return: str or None
    """
    logging.debug("Get the used port in migration")
    for func_ref, func_return in func_returns.items():
        if func_ref.__name__ == 'check_established':
            logging.debug("Return port:%s", func_return)
            return func_return

    return None


def check_vm_state_after_abort(vm_name, vm_state_after_abort, src_uri, dest_uri, test):
    """
    Check the VM state after domjobabort the migration

    :param vm_name: str, vm name
    :param vm_state_after_abort: str, like "{'source': 'running', 'target': 'nonexist'}"
                                 source: local host, target: remote host
    :param src_uri: uri for source host
    :param dest_uri: uri for target host
    :param test: test object
    """
    state_dict = eval(vm_state_after_abort)
    logging.debug("Check guest state should be {} on source host".format(state_dict['source']))
    libvirt.check_vm_state(vm_name, state=state_dict['source'], uri=src_uri)
    logging.debug("Check guest persistent on source host")
    cmd_res = virsh.domstats(vm_name, '--list-persistent', debug=True, ignore_status=False)
    if not cmd_res.stdout_text.count(vm_name):
        test.fail("The guest is expected to be persistent on source host, but it isn't")
    logging.debug("Check guest state should be {} on target host".format(state_dict['target']))
    if state_dict['target'] == 'nonexist':
        if virsh.domain_exists(vm_name, uri=dest_uri):
            test.fail("The domain on target host is found, but expected not")
    else:
        libvirt.check_vm_state(vm_name, state=state_dict['target'], uri=dest_uri)


def update_local_or_both_conf_file(params, conf_type='qemu'):
    """
    Read and update used parameter for the configuration file

    :param params: dict, parameters used
    :param conf_type: str, the configuration type
    :return: utils_config.LibvirtConfigCommon object
    """
    logging.debug("Update configuration file")
    migrate_tls_x509_verify = params.get('migrate_tls_x509_verify')
    default_tls_x509_verify = params.get('default_tls_x509_verify')
    qemu_conf_src = eval(params.get("qemu_conf_src", "{}"))
    mode = 'both'
    conf_dict = {}

    if migrate_tls_x509_verify:
        conf_dict.update({'migrate_tls_x509_verify': migrate_tls_x509_verify})
    if default_tls_x509_verify:
        conf_dict.update({'default_tls_x509_verify': default_tls_x509_verify})
    # If qemu_conf_src is defined, we only support local update
    if qemu_conf_src:
        mode = 'local'
        conf_dict.update(qemu_conf_src)
    if len(conf_dict) > 0:
        return libvirt.customize_libvirt_config(conf_dict,
                                                config_type=conf_type,
                                                remote_host=True if mode == 'both' else False,
                                                extra_params=params)
    else:
        logging.debug("No key/value configuration is given.")
        return None


def recover_config_file(conf_obj, params):
    """
    Recover the configuration files

    :param conf_obj: utils_config.LibvirtConfigCommon object to be restored
    :param params: dict, parameters used
    """
    if not conf_obj:
        logging.debug("No configuration object to recover")
        return
    logging.debug("Recover the configuration file")
    qemu_conf_src = eval(params.get("qemu_conf_src", "{}"))
    is_remote = False if qemu_conf_src else True
    libvirt.customize_libvirt_config(None,
                                     remote_host=is_remote,
                                     is_recover=True,
                                     extra_params=params,
                                     config_object=conf_obj)


def setup_loop_dev(block_device, remote_params=None):
    """
    Setup a loop device  for test

    :param block_device: block device name
    :param remote_params: Dict of parameters, which should include:
                          server_ip, server_user, server_pwd
    """
    loop_dev = None
    # Find a free loop device
    cmd = "losetup --find"
    if remote_params:
        loop_dev = remote_old.run_remote_cmd(cmd, remote_params, ignore_status=False).stdout_text.strip()
    else:
        loop_dev = process.run(cmd, shell=True).stdout_text.strip()
    logging.debug("loop dev: %s", loop_dev)
    # Setup a loop device
    cmd = 'losetup %s %s' % (loop_dev, block_device)
    if remote_params:
        remote_old.run_remote_cmd(cmd, remote_params, ignore_status=False)
    else:
        process.run(cmd, shell=True)
    return loop_dev


def prepare_block_device(params, vm_name):
    """
    Prepare block_device for test

    :param params: dict, parameters used
    :param vm_name: vm name
    """
    target_dev = params.get("target_dev")
    block_device = params.get("nfs_mount_dir") + '/' + params.get("block_device_name")
    libvirt.create_local_disk("file", block_device, '1', "qcow2")

    source_loop_dev = setup_loop_dev(block_device)
    target_loop_dev = setup_loop_dev(block_device, remote_params=params)

    # Attach block device to guest
    result = virsh.attach_disk(vm_name, source_loop_dev, target_dev, debug=True)
    libvirt.check_exit_status(result)
    return (source_loop_dev, target_loop_dev)


def run(test, params, env):
    """
    Run the test

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    libvirt_version.is_libvirt_feature_supported(params)

    vm_name = params.get("migrate_main_vm")
    vm = env.get_vm(vm_name)
    vm.verify_alive()
    bk_uri = vm.connect_uri

    migration_test = migration.MigrationTest()
    migration_test.check_parameters(params)
    extra_args = migration_test.update_virsh_migrate_extra_args(params)

    extra = params.get("virsh_migrate_extra")
    postcopy_options = params.get("postcopy_options")
    if postcopy_options:
        extra = "%s %s" % (extra, postcopy_options)
    params["virsh_migrate_desturi"] = libvirt_vm.complete_uri(
        params.get("migrate_dest_host"))
    dest_uri = params.get("virsh_migrate_desturi")
    options = params.get("virsh_migrate_options",
                         "--live --p2p --persistent --verbose")
    virsh_options = params.get("virsh_options", "")
    stress_package = params.get("stress_package")
    action_during_mig = params.get("action_during_mig")
    migrate_speed = params.get("migrate_speed")
    migrate_speed_again = params.get("migrate_speed_again")
    migrate_again = "yes" == params.get("migrate_again", "no")
    vm_state_after_abort = params.get("vm_state_after_abort")
    return_port = "yes" == params.get("return_port", "no")
    params['server_pwd'] = params.get("migrate_dest_pwd")
    params['server_ip'] = params.get("migrate_dest_host")
    params['server_user'] = params.get("remote_user", "root")
    is_storage_migration = True if extra.count('--copy-storage-all') else False
    setup_tls = "yes" == params.get("setup_tls", "no")
    qemu_conf_dest = params.get("qemu_conf_dest", "{}")
    migrate_tls_force_default = "yes" == params.get("migrate_tls_force_default", "no")
    poweroff_src_vm = "yes" == params.get("poweroff_src_vm", "no")
    check_port = "yes" == params.get("check_port", "no")
    server_ip = params.get("migrate_dest_host")
    server_user = params.get("remote_user", "root")
    server_pwd = params.get("migrate_dest_pwd")
    server_params = {'server_ip': server_ip,
                     'server_user': server_user,
                     'server_pwd': server_pwd}
    qemu_conf_list = eval(params.get("qemu_conf_list", "[]"))
    qemu_conf_path = params.get("qemu_conf_path")
    min_port = params.get("min_port")
    status_error = "yes" == params.get("status_error", "no")
    set_migration_host = "yes" == params.get("set_migration_host", "no")
    src_hosts_dict = eval(params.get("src_hosts_conf", "{}"))
    exec_statistics_cmd = "yes" == params.get("exec_statistics_cmd", "no")

    vm_session = None
    qemu_conf_remote = None
    (remove_key_local, remove_key_remote) = (None, None)
    source_loop_dev = None
    target_loop_dev = None

    # For safety reasons, we'd better back up  xmlfile.
    new_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    orig_config_xml = new_xml.copy()
    local_both_conf_obj = None
    remote_file_list = []
    conn_obj_list = []

    try:
        # Setup default value for migrate_tls_force
        if migrate_tls_force_default:
            value_list = ["migrate_tls_force"]
            # Setup migrate_tls_force default value on remote
            server_params['file_path'] = qemu_conf_path
            remove_key_remote = libvirt_config.remove_key_in_conf(value_list,
                                                                  "qemu",
                                                                  remote_params=server_params)
            # Setup migrate_tls_force default value on local
            remove_key_local = libvirt_config.remove_key_in_conf(value_list,
                                                                 "qemu")

        if set_migration_host:
            value_list = ["migration_host"]
            server_params['file_path'] = qemu_conf_path
            remove_key_remote = libvirt_config.remove_key_in_conf(value_list,
                                                                  "qemu",
                                                                  remote_params=server_params)
            # backup /etc/hosts
            backup_hosts = "cp -f /etc/hosts /etc/hosts.bak"
            remote_old.run_remote_cmd(backup_hosts, params, ignore_status=False)
            if not utils_net.map_hostname_ipaddress(src_hosts_dict):
                test.error("Failed to set /etc/hosts on source host.")

        if check_port:
            server_params['file_path'] = qemu_conf_path
            remove_key_remote = libvirt_config.remove_key_in_conf(qemu_conf_list,
                                                                  "qemu",
                                                                  remote_params=server_params)

        # Update only remote qemu conf
        if qemu_conf_dest:
            qemu_conf_remote = libvirt_remote.update_remote_file(
                server_params, qemu_conf_dest, qemu_conf_path)
        # Update local or both sides configuration files
        local_both_conf_obj = update_local_or_both_conf_file(params)
        # Setup TLS
        if setup_tls:
            conn_obj_list.append(migration_base.setup_conn_obj('tls',
                                                               params,
                                                               test))
        # Update guest disk xml
        if not is_storage_migration:
            libvirt.set_vm_disk(vm, params)
        else:
            remote_file_list.append(libvirt_disk.create_remote_disk_by_same_metadata(vm,
                                                                                     params))
        if check_port:
            # Create a remote runner
            runner_on_target = remote_old.RemoteRunner(host=server_ip,
                                                       username=server_user,
                                                       password=server_pwd)
            cmd = "nc -l -p %s &" % min_port
            remote_old.run_remote_cmd(cmd, params, runner_on_target, ignore_status=False)

        if not vm.is_alive():
            vm.start()

        logging.debug("Guest xml after starting:\n%s",
                      vm_xml.VMXML.new_from_dumpxml(vm_name))

        vm_session = vm.wait_for_login()
        if exec_statistics_cmd:
            (source_loop_dev, target_loop_dev) = prepare_block_device(params, vm_name)
            logging.debug("Guest xml with block device:\n%s",
                          vm_xml.VMXML.new_from_dumpxml(vm_name))
        if action_during_mig:
            if poweroff_src_vm:
                params.update({'vm_session': vm_session})
            action_during_mig = migration_base.parse_funcs(action_during_mig,
                                                           test, params)

        if stress_package:
            migration_test.run_stress_in_vm(vm, params)
        mode = 'both' if '--postcopy' in postcopy_options else 'precopy'
        if migrate_speed:
            migration_test.control_migrate_speed(vm_name,
                                                 int(migrate_speed),
                                                 mode)
        # Execute migration process
        migration_base.do_migration(vm, migration_test, None, dest_uri,
                                    options, virsh_options, extra,
                                    action_during_mig,
                                    extra_args)

        func_returns = dict(migration_test.func_ret)
        migration_test.func_ret.clear()
        logging.debug("Migration returns function results:%s", func_returns)
        if return_port:
            port_used = get_used_port(func_returns)
        if check_port:
            port_used = get_used_port(func_returns)
            if int(port_used) != int(min_port) + 1:
                test.fail("Wrong port for migration.")

        if vm_state_after_abort:
            check_vm_state_after_abort(vm_name, vm_state_after_abort,
                                       bk_uri, dest_uri, test)

        if migrate_again:
            if not status_error:
                virsh.destroy(vm_name, uri=dest_uri, debug=True, ignore_status=True)
            if not vm.is_alive():
                vm.connect_uri = bk_uri
                vm.start()
            vm_session = vm.wait_for_login()
            action_during_mig = migration_base.parse_funcs(params.get('action_during_mig_again'),
                                                           test, params)
            extra_args['status_error'] = params.get("migrate_again_status_error", "no")

            if params.get("virsh_migrate_extra_mig_again"):
                extra = params.get("virsh_migrate_extra_mig_again")

            if params.get('scp_list_client_again'):
                params['scp_list_client'] = params.get('scp_list_client_again')
                # Recreate tlsconnection object using new parameter values
                conn_obj_list.append(migration_base.setup_conn_obj('tls',
                                                                   params,
                                                                   test))

            if migrate_speed_again:
                migration_test.control_migrate_speed(vm_name,
                                                     int(migrate_speed_again),
                                                     mode)

            migration_base.do_migration(vm, migration_test, None, dest_uri,
                                        options, virsh_options,
                                        extra, action_during_mig,
                                        extra_args)
            if return_port:
                func_returns = dict(migration_test.func_ret)
                logging.debug("Migration returns function "
                              "results:%s", func_returns)
                port_second = get_used_port(func_returns)
                if port_used != port_second:
                    test.fail("Expect same port '{}' is used as previous one, "
                              "but found new one '{}'".format(port_used,
                                                              port_second))
                else:
                    logging.debug("Same port '%s' was used as "
                                  "expected", port_second)
        if int(migration_test.ret.exit_status) == 0:
            migration_test.post_migration_check([vm], params, uri=dest_uri)
    finally:
        logging.info("Recover test environment")
        vm.connect_uri = bk_uri
        if vm_session:
            vm_session.close()
        if exec_statistics_cmd:
            process.run('losetup -d %s' % source_loop_dev, shell=True)
            remote_old.run_remote_cmd('losetup -d %s' % target_loop_dev, params, ignore_status=False)

        # Clean VM on destination and source
        migration_test.cleanup_vm(vm, dest_uri)
        # Restore remote qemu conf and restart libvirtd
        if qemu_conf_remote:
            logging.debug("Recover remote qemu configurations")
            del qemu_conf_remote
        # Restore /etc/hosts
        if set_migration_host:
            restore_hosts = "mv -f /etc/hosts.bak /etc/hosts"
            remote_old.run_remote_cmd(restore_hosts, params, ignore_status=False)
        # Restore local or both sides conf and restart libvirtd
        recover_config_file(local_both_conf_obj, params)
        if remove_key_remote:
            del remove_key_remote
        if remove_key_local:
            libvirt.customize_libvirt_config(None,
                                             config_object=remove_key_local,
                                             config_type='qemu')
        # Clean up connection object, like TLS
        migration_base.cleanup_conn_obj(conn_obj_list, test)

        for one_file in remote_file_list:
            if one_file:
                remote_old.run_remote_cmd("rm -rf %s" % one_file, params)

        orig_config_xml.sync()
