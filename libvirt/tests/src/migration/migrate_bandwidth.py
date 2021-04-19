import os
import logging
import time

from virttest import libvirt_vm
from virttest import defaults
from virttest import virsh
from virttest import utils_misc
from virttest import utils_split_daemons
from virttest import migration
from virttest import libvirt_version

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.utils_test import libvirt_domjobinfo
from virttest.utils_libvirt import libvirt_config


def run(test, params, env):
    """
    Test migration with specified max bandwidth
    1) Set both precopy and postcopy bandwidth by virsh migrate parameter
    2) Set bandwidth before migration starts by migrate parameter --postcopy-bandwidth
    3) Set bandwidth when migration is in post-copy phase
    4) Set bandwidth when migration is in pre-copy phase
    5) Set bandwidth when guest is running and before migration starts
    6) Set bandwidth before migration starts by migrate parameter --bandwidth
    7) Set bandwidth when guest is running and before migration starts
    8) Set bandwidth when guest is shutoff
    9) Do live migration with default max bandwidth

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """

    def get_speed(exp_migrate_speed):
        """
        Get migration speed and compare with value in exp_migrate_speed

        :params exp_migrate_speed: the dict of expected migration speed
        :raise: test.fail if speed does not match
        """
        if exp_migrate_speed.get("precopy_speed"):
            output = virsh.migrate_getspeed(vm_name,
                                            **virsh_args).stdout_text.strip()
            if exp_migrate_speed['precopy_speed'] != output:
                virsh.migrate_getspeed(vm_name, extra="--postcopy",
                                       **virsh_args)
                test.fail("Migration speed is expected to be '%s MiB/s', but "
                          "'%s MiB/s' found!"
                          % (exp_migrate_speed['precopy_speed'], output))
        if exp_migrate_speed.get("postcopy_speed"):
            output = virsh.migrate_getspeed(vm_name, extra="--postcopy",
                                            **virsh_args).stdout_text.strip()
            if exp_migrate_speed['postcopy_speed'] != output:
                test.fail("Prostcopy migration speed is expected to be '%s "
                          "MiB/s', but '%s MiB/s' found!"
                          % (exp_migrate_speed['postcopy_speed'], output))

    def check_bandwidth(params):
        """
        Check migration bandwidth

        :param params: the parameters used
        :raise: test.fail if migration bandwidth does not match expected values
        """
        exp_migrate_speed = eval(params.get('exp_migrate_speed', '{}'))
        migrate_postcopy_cmd = "yes" == params.get("migrate_postcopy_cmd", "yes")
        if extra.count("bandwidth"):
            get_speed(exp_migrate_speed)
        if params.get("set_postcopy_in_precopy_phase"):
            virsh.migrate_setspeed(vm_name,
                                   params.get("set_postcopy_in_precopy_phase"),
                                   "--postcopy", **virsh_args)
            get_speed(exp_migrate_speed)

        params.update({'compare_to_value':
                       exp_migrate_speed.get("precopy_speed", "8796093022207")})

        if exp_migrate_speed.get("precopy_speed", "0") == "8796093022207":
            params.update({'domjob_ignore_status': True})
        libvirt_domjobinfo.check_domjobinfo(vm, params)

        if migrate_postcopy_cmd:
            if not utils_misc.wait_for(
               lambda: not virsh.migrate_postcopy(vm_name,
                                                  **virsh_args).exit_status, 5):
                test.fail("Failed to set migration postcopy.")

            if params.get("set_postcopy_in_postcopy_phase"):
                virsh.migrate_setspeed(vm_name,
                                       params.get("set_postcopy_in_postcopy_phase"),
                                       "--postcopy", **virsh_args)
                get_speed(exp_migrate_speed)
            time.sleep(5)

            if exp_migrate_speed.get("postcopy_speed"):
                params.update({'compare_to_value': exp_migrate_speed["postcopy_speed"]})
                params.update({'domjob_ignore_status': False})
            libvirt_domjobinfo.check_domjobinfo(vm, params)

    migration_test = migration.MigrationTest()
    migration_test.check_parameters(params)

    # Params for NFS shared storage
    shared_storage = params.get("migrate_shared_storage", "")
    if shared_storage == "":
        default_guest_asset = defaults.get_default_guest_os_info()['asset']
        default_guest_asset = "%s.qcow2" % default_guest_asset
        shared_storage = os.path.join(params.get("nfs_mount_dir"),
                                      default_guest_asset)
        logging.debug("shared_storage:%s", shared_storage)

    # Params to update disk using shared storage
    params["disk_type"] = "file"
    params["disk_source_protocol"] = "netfs"
    params["mnt_path_name"] = params.get("nfs_mount_dir")

    # Local variables
    server_ip = params.get("server_ip")
    server_user = params.get("server_user", "root")
    server_pwd = params.get("server_pwd")
    virsh_args = {"debug": True}
    virsh_options = params.get("virsh_options", "")
    extra = params.get("virsh_migrate_extra")
    options = params.get("virsh_migrate_options", "--live --verbose")
    jobinfo_item = params.get("jobinfo_item", 'Memory bandwidth:')
    set_postcopy_speed_before_mig = params.get("set_postcopy_speed_before_mig")
    set_precopy_speed_before_mig = params.get("set_precopy_speed_before_mig")
    set_precopy_speed_before_vm_start = params.get("set_precopy_speed_before_vm_start")
    stress_package = params.get("stress_package")
    exp_migrate_speed = eval(params.get('exp_migrate_speed', '{}'))
    log_file = params.get("log_outputs", "/var/log/libvirt/libvirt_daemons.log")
    check_str_local_log = params.get("check_str_local_log", "")
    libvirtd_conf_dict = eval(params.get("libvirtd_conf_dict", '{}'))
    action_during_mig = check_bandwidth
    params.update({"action_during_mig_params_exists": "yes"})
    extra_args = migration_test.update_virsh_migrate_extra_args(params)

    libvirtd_conf = None
    mig_result = None
    remove_dict = {}
    src_libvirt_file = None

    if not libvirt_version.version_compare(6, 0, 0):
        test.cancel("This libvirt version doesn't support "
                    "postcopy migration bandwidth function.")

    # params for migration connection
    params["virsh_migrate_desturi"] = libvirt_vm.complete_uri(
                                       params.get("migrate_dest_host"))
    dest_uri = params.get("virsh_migrate_desturi")

    vm_name = params.get("migrate_main_vm")
    vm = env.get_vm(vm_name)
    vm.verify_alive()

    postcopy_options = params.get("postcopy_options")
    if postcopy_options:
        extra = "%s %s" % (extra, postcopy_options)

    # For safety reasons, we'd better back up  xmlfile.
    new_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    orig_config_xml = new_xml.copy()

    try:
        # Change the disk of the vm
        libvirt.set_vm_disk(vm, params)

        # Update libvirtd configuration
        if libvirtd_conf_dict:
            if os.path.exists(log_file):
                logging.debug("Delete local libvirt log file '%s'", log_file)
                os.remove(log_file)
            logging.debug("Update libvirtd configuration file")
            conf_type = "libvirtd"
            if utils_split_daemons.is_modular_daemon():
                conf_type = "virtqemud"
            libvirtd_conf = libvirt.customize_libvirt_config(libvirtd_conf_dict,
                                                             conf_type,)

        if set_precopy_speed_before_vm_start:
            if vm.is_alive():
                vm.destroy()
            virsh.migrate_setspeed(vm_name, set_precopy_speed_before_vm_start,
                                   **virsh_args)

        if not vm.is_alive():
            vm.start()

        logging.debug("Guest xml after starting:\n%s",
                      vm_xml.VMXML.new_from_dumpxml(vm_name))

        # Check local guest network connection before migration
        vm.wait_for_login(restart_network=True).close()
        migration_test.ping_vm(vm, params)

        remove_dict = {"do_search": '{"%s": "ssh:/"}' % dest_uri}
        src_libvirt_file = libvirt_config.remove_key_for_modular_daemon(
            remove_dict)

        if any([set_precopy_speed_before_mig, set_postcopy_speed_before_mig]):
            if set_precopy_speed_before_mig:
                virsh.migrate_setspeed(vm_name, set_precopy_speed_before_mig,
                                       **virsh_args)

            if set_postcopy_speed_before_mig:
                virsh.migrate_setspeed(vm_name, set_postcopy_speed_before_mig,
                                       "--postcopy", **virsh_args)
            get_speed(exp_migrate_speed)

        if stress_package:
            migration_test.run_stress_in_vm(vm, params)

        # Execute migration process
        vms = [vm]

        migration_test.do_migration(vms, None, dest_uri, 'orderly',
                                    options, thread_timeout=900,
                                    ignore_status=True, virsh_opt=virsh_options,
                                    func=action_during_mig,
                                    extra_opts=extra,
                                    **extra_args)

        if int(migration_test.ret.exit_status) == 0:
            migration_test.ping_vm(vm, params, uri=dest_uri)

        if check_str_local_log:
            libvirt.check_logfile(check_str_local_log, log_file)

    finally:
        logging.debug("Recover test environment")
        # Clean VM on destination and source
        migration_test.cleanup_vm(vm, dest_uri)

        logging.info("Recover VM XML configration")
        orig_config_xml.sync()

        if libvirtd_conf:
            logging.debug("Recover the configurations")
            libvirt.customize_libvirt_config(None, is_recover=True,
                                             config_object=libvirtd_conf)
        if src_libvirt_file:
            src_libvirt_file.restore()

        logging.info("Remove local NFS image")
        source_file = params.get("source_file")
        libvirt.delete_local_disk("file", path=source_file)
