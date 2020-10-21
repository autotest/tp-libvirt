import logging
import threading
import time
import os

from avocado.utils import process

from virttest import virsh
from virttest import ssh_key
from virttest import utils_misc
from virttest import libvirt_version

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


# To get result in thread, using global parameters
# result of virsh migrate-setmaxdowntime command
global ret_setmmdt
# result of virsh migrate command
global ret_migration
ret_setmmdt = False
ret_migration = False


def thread_func_setmmdt(domain, downtime, extra, dargs):
    """
    Thread for virsh migrate-setmaxdowntime command.
    """
    global ret_setmmdt
    result = virsh.migrate_setmaxdowntime(domain, downtime, extra,
                                          **dargs)
    if result.exit_status:
        ret_setmmdt = False
        logging.error("Set max migration downtime failed.")
    else:
        ret_setmmdt = True


def thread_func_live_migration(vm, dest_uri, dargs):
    """
    Thread for virsh migrate command.
    """
    # Migrate the domain.
    options = "--live --unsafe"
    postcopy_options = dargs.get("postcopy_options")
    if postcopy_options:
        options = "%s %s" % (options, postcopy_options)
    extra = dargs.get("extra")
    global ret_migration
    result = vm.migrate(dest_uri, options, extra, **dargs)
    if result.exit_status:
        logging.error("Migrate %s to %s failed." % (vm.name, dest_uri))
        return

    if vm.is_alive():  # vm.connect_uri has been updated to dest_uri
        logging.info("Alive guest found on destination %s." % dest_uri)
    else:
        logging.error("VM not alive on destination %s" % dest_uri)
        return
    ret_migration = True


def cleanup_dest(vm, src_uri, dest_uri):
    """
    Cleanup migrated guest on destination.
    Then reset connect uri to src_uri
    """
    vm.connect_uri = dest_uri
    if vm.exists():
        if vm.is_persistent():
            vm.undefine()
        if vm.is_alive():
            vm.destroy()
    # Set connect uri back to local uri
    vm.connect_uri = src_uri


def cleanup_daemon_log(log_file):
    """
    Remove existing daemon log file on local host

    :param log_file: log file with absolute path
    """
    if os.path.exists(log_file):
        logging.debug("Delete local log file '%s'", log_file)
        os.remove(log_file)


def update_config_file(conf_type, conf_dict, scp_to_remote=False,
                       remote_params=None):
    """
    Update the specified configuration file

    :param conf_type: String type, conf type like libvirtd, qemu
    :param conf_dict: dict of parameters to set
    :param scp_to_remote: True to also update in remote host
    :param remote_params: The dict including parameters to connect remote host
    :return: utils_config.LibvirtConfigCommon object
    """

    logging.debug("Update configuration file")
    updated_conf = libvirt.customize_libvirt_config(conf_dict,
                                                    config_type=conf_type,
                                                    remote_host=scp_to_remote,
                                                    extra_params=remote_params)
    return updated_conf


def run(test, params, env):
    """
    Test virsh migrate-setmaxdowntime command.

    1) Prepare migration environment
    2) Start migration and set migrate-maxdowntime
    3) Cleanup environment(migrated vm on destination)
    4) Check result
    """
    dest_uri = params.get(
        "virsh_migrate_dest_uri", "qemu+ssh://MIGRATE_EXAMPLE/system")
    src_uri = params.get(
        "virsh_migrate_src_uri", "qemu+ssh://MIGRATE_EXAMPLE/system")
    if dest_uri.count('///') or dest_uri.count('MIGRATE_EXAMPLE'):
        test.cancel("Set your destination uri first.")
    if src_uri.count('MIGRATE_EXAMPLE'):
        test.cancel("Set your source uri first.")
    if src_uri == dest_uri:
        test.cancel("You should not set dest uri same as local.")
    vm_ref = params.get("setmmdt_vm_ref", "domname")
    pre_vm_state = params.get("pre_vm_state", "running")
    status_error = "yes" == params.get("status_error", "no")
    do_migrate = "yes" == params.get("do_migrate", "yes")
    migrate_maxdowntime = params.get("migrate_maxdowntime", 1.000)
    if (migrate_maxdowntime == ""):
        downtime = ""
    else:
        downtime = int(float(migrate_maxdowntime)) * 1000
    extra = params.get("setmmdt_extra")

    # For --postcopy enable
    postcopy_options = params.get("postcopy_options", "")

    # A delay between threads
    delay_time = int(params.get("delay_time", 1))
    # timeout of threads
    thread_timeout = 180

    vm_name = params.get("migrate_main_vm")
    vm = env.get_vm(vm_name)
    domuuid = vm.get_uuid()

    grep_str_local = params.get("grep_str_from_local_libvirt_log", "")

    # For safety reasons, we'd better back up original guest xml
    orig_config_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    if not orig_config_xml:
        test.error("Backing up xmlfile failed.")

    # Clean up daemon log
    log_file = params.get("log_file")
    cleanup_daemon_log(log_file)

    # Config daemon log
    log_conf_dict = eval(params.get("log_conf_dict", '{}'))
    log_conf_type = params.get("log_conf_type")
    log_conf = None
    log_conf = update_config_file(log_conf_type, log_conf_dict,
                                  scp_to_remote=False, remote_params=None)

    # Params to update disk using shared storage
    params["disk_type"] = "file"
    params["disk_source_protocol"] = "netfs"
    params["mnt_path_name"] = params.get("nfs_mount_dir")

    # Params to setup SSH connection
    params["server_ip"] = params.get("migrate_dest_host")
    params["server_pwd"] = params.get("migrate_dest_pwd")
    params["client_ip"] = params.get("migrate_source_host")
    params["client_pwd"] = params.get("migrate_source_pwd")
    params["nfs_client_ip"] = params.get("migrate_dest_host")
    params["nfs_server_ip"] = params.get("migrate_source_host")

    # Params to enable SELinux boolean on remote host
    params["remote_boolean_varible"] = "virt_use_nfs"
    params["remote_boolean_value"] = "on"
    params["set_sebool_remote"] = "yes"

    remote_host = params.get("migrate_dest_host")
    username = params.get("migrate_dest_user", "root")
    password = params.get("migrate_dest_pwd")
    # Config ssh autologin for remote host
    ssh_key.setup_ssh_key(remote_host, username, password, port=22)

    setmmdt_dargs = {'debug': True, 'ignore_status': True, 'uri': src_uri}
    migrate_dargs = {'debug': True, 'ignore_status': True,
                     'postcopy_options': postcopy_options}

    try:
        # Update the disk using shared storage
        libvirt.set_vm_disk(vm, params)

        if not vm.is_alive():
            vm.start()
        vm.wait_for_login()
        domid = vm.get_id()

        # Confirm how to reference a VM.
        if vm_ref == "domname":
            vm_ref = vm_name
        elif vm_ref == "domid":
            vm_ref = domid
        elif vm_ref == "domuuid":
            vm_ref = domuuid

        # Prepare vm state
        if pre_vm_state == "paused":
            vm.pause()
        elif pre_vm_state == "shutoff":
            vm.destroy(gracefully=False)
            # Ensure VM in 'shut off' status
            utils_misc.wait_for(lambda: vm.state() == "shut off", 30)

        # Set max migration downtime must be during migration
        # Using threads for synchronization
        threads = []
        if do_migrate:
            threads.append(threading.Thread(target=thread_func_live_migration,
                                            args=(vm, dest_uri,
                                                  migrate_dargs)))

        threads.append(threading.Thread(target=thread_func_setmmdt,
                                        args=(vm_ref, downtime, extra,
                                              setmmdt_dargs)))
        for thread in threads:
            thread.start()
            # Migration must be executing before setting maxdowntime
            time.sleep(delay_time)
        # Wait until thread is over
        for thread in threads:
            thread.join(thread_timeout)

        if (status_error is False or do_migrate is False):
            logging.debug("To match the expected pattern '%s' ...",
                          grep_str_local)
            cmd = "grep -E '%s' %s" % (grep_str_local, log_file)
            cmdResult = process.run(cmd, shell=True, verbose=False)
            logging.debug(cmdResult)

    finally:
        # Clean up.
        if do_migrate:
            logging.debug("Cleanup VM on remote host...")
            cleanup_dest(vm, src_uri, dest_uri)

        if orig_config_xml:
            logging.debug("Recover VM XML...")
            orig_config_xml.sync()

        logging.info("Remove the NFS image...")
        source_file = params.get("source_file")
        libvirt.delete_local_disk("file", path=source_file)

        # Recover libvirtd service configuration on local
        if log_conf:
            logging.debug("Recover local libvirtd configuration...")
            libvirt.customize_libvirt_config(None,
                                             remote_host=False,
                                             extra_params=params,
                                             is_recover=True,
                                             config_object=log_conf)
            cleanup_daemon_log(log_file)

    # Check results.
    if status_error:
        if ret_setmmdt:
            if not do_migrate and libvirt_version.version_compare(1, 2, 9):
                # https://bugzilla.redhat.com/show_bug.cgi?id=1146618
                # Commit fe808d9 fix it and allow setting migration
                # max downtime any time since libvirt-1.2.9
                logging.info("Libvirt version is newer than 1.2.9,"
                             "Allow set maxdowntime while VM isn't migrating")
            else:
                test.fail("virsh migrate-setmaxdowntime succeed "
                          "but not expected.")
    else:
        if do_migrate and not ret_migration:
            test.fail("Migration failed.")

        if not ret_setmmdt:
            test.fail("virsh migrate-setmaxdowntime failed.")
