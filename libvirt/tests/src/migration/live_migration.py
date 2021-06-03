import logging

from virttest import libvirt_vm
from virttest import migration
from virttest import virsh
from virttest import libvirt_remote
from virttest import libvirt_version

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.utils_conn import TLSConnection
from virttest.utils_libvirt import libvirt_config

from provider.migration import migration_base


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


def run(test, params, env):
    """
    Run the test

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
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
    #action_params_map = params.get('action_params_map')
    migrate_speed = params.get("migrate_speed")
    migrate_again = "yes" == params.get("migrate_again", "no")
    vm_state_after_abort = params.get("vm_state_after_abort")
    return_port = params.get("return_port")

    if action_during_mig:
        action_during_mig = migration_base.parse_funcs(action_during_mig,
                                                       test, params)
    setup_tls = params.get("setup_tls", "no")
    if setup_tls == "yes":
        if not libvirt_version.version_compare(6, 9, 0):
            test.cancel("Cannot support migrate_tls_force in this libvirt version.")

    qemu_conf_src = eval(params.get("qemu_conf_src", "{}"))
    qemu_conf_dest = params.get("qemu_conf_dest", "{}")
    status_error = "yes" == params.get("status_error", "no")
    migrate_tls_force_default = "yes" == params.get("migrate_tls_force_default", "no")
    server_ip = params.get("server_ip")
    server_user = params.get("server_user")
    server_pwd = params.get("server_pwd")
    server_params = {'server_ip': server_ip,
                     'server_user': server_user,
                     'server_pwd': server_pwd}
    tls_obj = None
    qemu_conf_local = None
    qemu_conf_remote = None

    # For safety reasons, we'd better back up  xmlfile.
    new_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    orig_config_xml = new_xml.copy()

    try:
        libvirt.set_vm_disk(vm, params)

        # Setup libvirtd remote connection TLS connection env
        if setup_tls == "yes":
            tls_obj = TLSConnection(params)
            tls_obj.auto_recover = True
            tls_obj.conn_setup()

        # Setup default value for migrate_tls_force
        if migrate_tls_force_default:
            value_list = ["migrate_tls_force"]
            # Setup migrate_tls_force default value on remote
            server_params['file_path'] = "/etc/libvirt/qemu.conf"
            libvirt_config.remove_key_in_conf(value_list, "qemu", remote_params=server_params)
            # Setup migrate_tls_force default value on local
            libvirt_config.remove_key_in_conf(value_list, "qemu")

        # Update remote qemu conf
        if qemu_conf_dest:
            qemu_conf_remote = libvirt_remote.update_remote_file(
                server_params, qemu_conf_dest, "/etc/libvirt/qemu.conf")
        # Update local qemu conf
        if qemu_conf_src:
            qemu_conf_local = libvirt.customize_libvirt_config(qemu_conf_src, "qemu")

        if not vm.is_alive():
            vm.start()

        logging.debug("Guest xml after starting:\n%s",
                      vm_xml.VMXML.new_from_dumpxml(vm_name))

        vm.wait_for_login().close()

        if stress_package:
            migration_test.run_stress_in_vm(vm, params)
        if migrate_speed:
            mode = 'both' if '--postcopy' in postcopy_options else 'precopy'
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

        if vm_state_after_abort:
            check_vm_state_after_abort(vm_name, vm_state_after_abort,
                                       bk_uri, dest_uri, test)

        if migrate_again:
            action_during_mig = migration_base.parse_funcs(params.get('action_during_mig_again'),
                                                           test,
                                                           params)
            extra_args['status_error'] = params.get("migrate_again_status_error", "no")
            if params.get("virsh_migrate_extra_mig_again"):
                extra = params.get("virsh_migrate_extra_mig_again")
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

        migration_test.post_migration_check([vm], params, uri=dest_uri)
    finally:
        logging.info("Recover test environment")
        vm.connect_uri = bk_uri
        # Clean VM on destination and source
        migration_test.cleanup_vm(vm, dest_uri)

        # Clean up TLS test env:
        if setup_tls and tls_obj:
            logging.debug("Clean up TLS object")
            del tls_obj

        # Restore local qemu conf and restart libvirtd
        if qemu_conf_local:
            logging.debug("Recover local qemu configurations")
            libvirt.customize_libvirt_config(None, config_type="qemu", is_recover=True,
                                             config_object=qemu_conf_local)
        # Restore remote qemu conf and restart libvirtd
        if qemu_conf_remote:
            logging.debug("Recover remote qemu configurations")
            libvirt.customize_libvirt_config(None,
                                             config_type="qemu",
                                             remote_host=True,
                                             extra_params=server_params,
                                             is_recover=True,
                                             config_object=None)
        orig_config_xml.sync()
