import logging as log
import os

from pwd import getpwuid
from grp import getgrgid

from virttest import libvirt_vm
from virttest import migration
from virttest import virsh
from virttest import libvirt_version

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt

from provider.migration import migration_base


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def check_image_ownership(vm_name, exp_ownership, test):
    """
    Check ownership of image

    :param vm_name: vm name
    :param exp_ownership: the expected ownership
    :param test: test object
    """
    sourcelist = vm_xml.VMXML.get_disk_source(vm_name)
    disk_source = sourcelist[0].find('source').get('file')
    logging.debug("image file: %s" % disk_source)
    image_ownership = "%s:%s" % (getpwuid(os.stat(disk_source).st_uid).pw_name,
                                 getgrgid(os.stat(disk_source).st_gid).gr_name)
    logging.debug("image ownership: %s" % image_ownership)
    if image_ownership != exp_ownership:
        test.fail("The ownership {} is not expected, it should be {}."
                  .format(image_ownership, exp_ownership))


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
    action_during_mig = params.get("action_during_mig")
    migrate_speed = params.get("migrate_speed")
    migrate_again = "yes" == params.get("migrate_again", "no")
    vm_state_after_abort = params.get("vm_state_after_abort")

    kill_service = "yes" == params.get("kill_service", "no")
    expected_image_ownership = params.get("expected_image_ownership")
    service_name = params.get("service_name", "libvirtd")
    service_on_dst = "yes" == params.get("service_on_dst", "no")
    server_ip = params.get("remote_ip")
    server_user = params.get("remote_user", "root")
    server_pwd = params.get("remote_pwd")

    # For safety reasons, we'd better back up  xmlfile.
    new_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    orig_config_xml = new_xml.copy()

    try:
        # Update guest disk xml
        libvirt.set_vm_disk(vm, params)

        logging.debug("Guest xml after starting:\n%s",
                      vm_xml.VMXML.new_from_dumpxml(vm_name))

        vm.wait_for_login().close()

        if kill_service:
            check_image_ownership(vm_name, expected_image_ownership, test)

        if migrate_speed:
            mode = 'both' if '--postcopy' in postcopy_options else 'precopy'
            migration_test.control_migrate_speed(vm_name,
                                                 int(migrate_speed),
                                                 mode)

        if action_during_mig:
            action_during_mig = migration_base.parse_funcs(action_during_mig,
                                                           test, params)

        # Execute migration process
        do_mig_param = {"vm": vm, "mig_test": migration_test, "src_uri": None, "dest_uri": dest_uri,
                        "options": options, "virsh_options": virsh_options, "extra": extra,
                        "action_during_mig": action_during_mig, "extra_args": extra_args}
        migration_base.do_migration(**do_mig_param)

        func_returns = dict(migration_test.func_ret)
        migration_test.func_ret.clear()
        logging.debug("Migration returns function results:%s", func_returns)

        if vm_state_after_abort:
            check_vm_state_after_abort(vm_name, vm_state_after_abort,
                                       bk_uri, dest_uri, test)

        if kill_service:
            check_image_ownership(vm_name, expected_image_ownership, test)

        if migrate_again:
            action_during_mig = migration_base.parse_funcs(params.get('action_during_mig_again'),
                                                           test, params)
            extra_args['status_error'] = params.get("migrate_again_status_error", "no")
            do_mig_param = {"vm": vm, "mig_test": migration_test, "src_uri": None, "dest_uri": dest_uri,
                            "options": options, "virsh_options": virsh_options, "extra": extra,
                            "action_during_mig": action_during_mig, "extra_args": extra_args}
            migration_base.do_migration(**do_mig_param)
        if int(migration_test.ret.exit_status) == 0:
            migration_test.post_migration_check([vm], params, dest_uri=dest_uri)
    finally:
        logging.info("Recover test environment")
        vm.connect_uri = bk_uri
        # Clean VM on destination and source
        migration_test.cleanup_vm(vm, dest_uri)

        orig_config_xml.sync()
