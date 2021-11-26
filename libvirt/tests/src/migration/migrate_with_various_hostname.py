import logging

from avocado.utils import process

from virttest import libvirt_vm
from virttest import migration
from virttest import remote as remote_old
from virttest import libvirt_version
from virttest import utils_libvirtd

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt

from provider.migration import migration_base


def get_hostname(test, remote_params=None):
    """
    Get hostname for source or dest host

    :param test: test object
    :param remote_params: Dict of remote host parameters, which should
                          include: server_ip, server_user, server_pwd
    """
    cmd = "hostname"
    if remote_params:
        ret = remote_old.run_remote_cmd(cmd, remote_params, ignore_status=False)
    else:
        ret = process.run(cmd, ignore_status=False, shell=True)
    output = ret.stdout_text.strip()
    if ret.exit_status:
        test.fail("Failed to run '%s': %s" % (cmd, output))
    logging.info("Get hostname: %s" % output)
    return output


def set_hostname(hostname, test, remote_params=None):
    """
    Set hostname for source or dest host

    :param hostname: string, hostname
    :param test: test object
    :param remote_params: Dict of remote host parameters, which should
                          include: server_ip, server_user, server_pwd
    """
    cmd = "hostnamectl set-hostname %s" % hostname
    if remote_params:
        ret = remote_old.run_remote_cmd(cmd, remote_params, ignore_status=False)
    else:
        ret = process.run(cmd, ignore_status=False, shell=True)
    output = ret.stdout_text.strip()
    if ret.exit_status:
        test.fail("Failed to run '%s': %s" % (cmd, output))
    logging.info("Set hostname: %s" % hostname)


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
    migrate_again = "yes" == params.get("migrate_again", "no")
    src_state = params.get("virsh_migrate_src_state", "shut off")
    set_src_and_dst_hostname = "yes" == params.get("set_src_and_dst_hostname", "no")
    src_hostname = params.get("src_hostname")
    dst_hostname = params.get("dst_hostname")
    server_ip = params.get("remote_ip")
    server_user = params.get("remote_user", "root")
    server_pwd = params.get("remote_pwd")
    server_params = {'server_ip': server_ip,
                     'server_user': server_user,
                     'server_pwd': server_pwd}

    dst_session = None
    dst_libvirtd = None
    src_libvirtd = None

    # For safety reasons, we'd better back up  xmlfile.
    new_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    orig_config_xml = new_xml.copy()

    try:
        # Update guest disk xml
        libvirt.set_vm_disk(vm, params)

        if set_src_and_dst_hostname:
            old_dst_hostname = get_hostname(test, remote_params=server_params)
            set_hostname(dst_hostname, test, remote_params=server_params)
            dst_session = remote_old.wait_for_login('ssh', server_ip, '22',
                                                    server_user, server_pwd,
                                                    r"[\#\$]\s*$")
            dst_libvirtd = utils_libvirtd.Libvirtd(session=dst_session)
            dst_libvirtd.restart()
            old_source_hostname = get_hostname(test)
            set_hostname(src_hostname, test)
            src_libvirtd = utils_libvirtd.Libvirtd()
            src_libvirtd.restart()

        if not vm.is_alive():
            vm.start()

        logging.debug("Guest xml after starting:\n%s",
                      vm_xml.VMXML.new_from_dumpxml(vm_name))

        vm.wait_for_login()

        # Execute migration process
        migration_base.do_migration(vm, migration_test, None, dest_uri,
                                    options, virsh_options, extra,
                                    None,
                                    extra_args)

        func_returns = dict(migration_test.func_ret)
        migration_test.func_ret.clear()
        logging.debug("Migration returns function results:%s", func_returns)

        if migrate_again:
            if not vm.is_alive():
                vm.start()
            vm.wait_for_login()
            extra_args['status_error'] = params.get("migrate_again_status_error", "no")

            if params.get("virsh_migrate_extra_mig_again"):
                extra = params.get("virsh_migrate_extra_mig_again")

            migration_base.do_migration(vm, migration_test, None, dest_uri,
                                        options, virsh_options,
                                        extra, None,
                                        extra_args)
        if int(migration_test.ret.exit_status) == 0:
            migration_test.post_migration_check([vm], params, uri=dest_uri)
        if not libvirt.check_vm_state(vm_name, state=src_state, uri=bk_uri):
            test.fail("Can't get the expected vm state '%s'" % src_state)
    finally:
        logging.info("Recover test environment")
        vm.connect_uri = bk_uri
        # Clean VM on destination and source
        migration_test.cleanup_vm(vm, dest_uri)

        if set_src_and_dst_hostname:
            set_hostname(old_dst_hostname, test, remote_params=server_params)
            if dst_libvirtd:
                dst_libvirtd.restart()
            if dst_session:
                dst_session.close()
            set_hostname(old_source_hostname, test)
            if src_libvirtd:
                src_libvirtd.restart()
        orig_config_xml.sync()
