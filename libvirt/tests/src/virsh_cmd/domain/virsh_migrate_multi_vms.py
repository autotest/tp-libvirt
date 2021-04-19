import logging
import threading
import time

from avocado.core import exceptions

from virttest import libvirt_vm
from virttest import virsh
from virttest import remote
from virttest import utils_test
from virttest import nfs
from virttest import ssh_key
from virttest import migration
from virttest.libvirt_xml import vm_xml


# To get result in thread, using global parameters
# Result of virsh migrate command
global ret_migration
# Result of virsh domjobabort
global ret_jobabort
# If downtime is tolerable
global ret_downtime_tolerable
# True means command executed successfully
ret_migration = True
ret_jobabort = True
ret_downtime_tolerable = True
flag_migration = True


def make_migration_options(method, optionstr="", timeout=60):
    """
    Analyse a string to options for migration.
    They are split by one space.

    :param method: migration method ie using p2p or p2p tunnelled or direct
    :param optionstr: a string contain all options and split by space
    :param timeout: timeout for migration.
    """
    options = ""
    migrate_exec = ""

    if method == "p2p":
        migrate_exec += " --p2p"
    elif method == "p2p_tunnelled":
        migrate_exec += " --p2p --tunnelled"
    elif method == "direct":
        migrate_exec += " --direct"
    else:
        # Default method or unknown method
        pass

    for option in optionstr.split():
        if option == "live":
            options += " --live"
        elif option == "persistent":
            options += " --persistent"
        elif option == "suspend":
            options += " --suspend"
        elif option == "change-protection":
            options += " --change-protection"
        elif option == "timeout":
            options += " --timeout %s" % timeout
        elif option == "unsafe":
            options += " --unsafe"
        elif option == "auto-converge":
            options += " --auto-converge"
        else:
            logging.debug("Do not support option '%s' yet." % option)
    return options + migrate_exec


def thread_func_ping(lrunner, rrunner, vm_ip, tolerable=5):
    """
    Check connectivity during migration: Ping vm every second, check whether
    the paused state is intolerable.
    """
    cmd = "ping -c 1 %s" % vm_ip
    time1 = None    # Flag the time local vm is down
    time2 = None    # Flag the time remote vm is up
    timeout = 360   # In case thread is not killed at the end of test
    global ret_downtime_tolerable
    while timeout:
        ls = lrunner.run(cmd, ignore_status=True).exit_status
        rs = rrunner.run(cmd, ignore_status=True).exit_status
        if ls and time1 is None:   # The first time local vm is not connective
            time1 = int(time.time())
        if not rs and time2 is None:  # The first time remote vm is connective
            time2 = int(time.time())
        if time1 is None or time2 is None:
            time.sleep(1)
            timeout -= 1
        else:
            if int(time2 - time1) > int(tolerable):
                logging.debug("The time local vm is down: %s", time1)
                logging.debug("The time remote vm is up: %s", time2)
                ret_downtime_tolerable = False
            break   # Got enough information, leaving thread anyway


def thread_func_jobabort(vm):
    global ret_jobabort
    if not vm.domjobabort():
        ret_jobabort = False


def multi_migration(vm, src_uri, dest_uri, options, migrate_type,
                    migrate_thread_timeout, jobabort=False,
                    lrunner=None, rrunner=None, status_error=None):
    """
    Migrate multiple vms simultaneously or not.

    :param vm: list of all vm instances
    :param src_uri: source ip address for migration
    :param dest_uri: destination ipaddress for migration
    :param options: options to be passed in migration command
    :param migrate_type: orderly or simultaneous migration type
    :param migrate_thread_timeout: thread timeout for migrating vms
    :param jobabort: If jobabort is True, run "virsh domjobabort vm_name"
               during migration.
    :param param timeout: thread's timeout
    :param lrunner: local session instance
    :param rrunner: remote session instance
    :param status_error: Whether expect error status
    """

    obj_migration = migration.MigrationTest()
    if migrate_type.lower() == "simultaneous":
        logging.info("Migrate vms simultaneously.")
        try:
            obj_migration.do_migration(vms=vm, srcuri=src_uri,
                                       desturi=dest_uri,
                                       migration_type="simultaneous",
                                       options=options,
                                       thread_timeout=migrate_thread_timeout,
                                       ignore_status=False,
                                       status_error=status_error)
            if jobabort:
                # To ensure Migration has been started.
                time.sleep(5)
                logging.info("Aborting job during migration.")
                jobabort_threads = []
                func = thread_func_jobabort
                for each_vm in vm:
                    jobabort_thread = threading.Thread(target=func,
                                                       args=(each_vm,))
                    jobabort_threads.append(jobabort_thread)
                    jobabort_thread.start()
                for jobabort_thread in jobabort_threads:
                    jobabort_thread.join(migrate_thread_timeout)
            ret_migration = True

        except Exception as info:
            raise exceptions.TestFail(info)

    elif migrate_type.lower() == "orderly":
        logging.info("Migrate vms orderly.")
        try:
            obj_migration.do_migration(vms=vm, srcuri=src_uri,
                                       desturi=dest_uri,
                                       migration_type="orderly",
                                       options=options,
                                       thread_timeout=migrate_thread_timeout,
                                       ignore_status=False,
                                       status_error=status_error)
            for each_vm in vm:
                ping_thread = threading.Thread(target=thread_func_ping,
                                               args=(lrunner, rrunner,
                                                     each_vm.get_address()))
                ping_thread.start()

        except Exception as info:
            raise exceptions.TestFail(info)

    if obj_migration.RET_MIGRATION:
        ret_migration = True
    else:
        ret_migration = False


def run(test, params, env):
    """
    Test migration of multi vms.
    """
    vm_names = params.get("migrate_vms").split()
    if len(vm_names) < 2:
        raise exceptions.TestSkipError("No multi vms provided.")

    # Prepare parameters
    method = params.get("virsh_migrate_method")
    jobabort = "yes" == params.get("virsh_migrate_jobabort", "no")
    options = params.get("virsh_migrate_options", "")
    status_error = "yes" == params.get("status_error", "no")
    remote_host = params.get("remote_host", "DEST_HOSTNAME.EXAMPLE.COM")
    local_host = params.get("local_host", "SOURCE_HOSTNAME.EXAMPLE.COM")
    host_user = params.get("host_user", "root")
    host_passwd = params.get("host_password", "PASSWORD")
    nfs_shared_disk = params.get("nfs_shared_disk", True)
    migration_type = params.get("virsh_migration_type", "simultaneous")
    migrate_timeout = int(params.get("virsh_migrate_thread_timeout", 900))
    migration_time = int(params.get("virsh_migrate_timeout", 60))

    # Params for NFS and SSH setup
    params["server_ip"] = params.get("migrate_dest_host")
    params["server_user"] = "root"
    params["server_pwd"] = params.get("migrate_dest_pwd")
    params["client_ip"] = params.get("migrate_source_host")
    params["client_user"] = "root"
    params["client_pwd"] = params.get("migrate_source_pwd")
    params["nfs_client_ip"] = params.get("migrate_dest_host")
    params["nfs_server_ip"] = params.get("migrate_source_host")
    desturi = libvirt_vm.get_uri_with_transport(transport="ssh",
                                                dest_ip=remote_host)
    srcuri = libvirt_vm.get_uri_with_transport(transport="ssh",
                                               dest_ip=local_host)

    # Don't allow the defaults.
    if srcuri.count('///') or srcuri.count('EXAMPLE'):
        raise exceptions.TestSkipError("The srcuri '%s' is invalid" % srcuri)
    if desturi.count('///') or desturi.count('EXAMPLE'):
        raise exceptions.TestSkipError("The desturi '%s' is invalid" % desturi)

    # Config ssh autologin for remote host
    ssh_key.setup_remote_ssh_key(remote_host, host_user, host_passwd, port=22,
                                 public_key="rsa")

    # Prepare local session and remote session
    localrunner = remote.RemoteRunner(host=remote_host, username=host_user,
                                      password=host_passwd)
    remoterunner = remote.RemoteRunner(host=remote_host, username=host_user,
                                       password=host_passwd)
    # Configure NFS in remote host
    if nfs_shared_disk:
        nfs_client = nfs.NFSClient(params)
        nfs_client.setup()

    # Prepare MigrationHelper instance
    vms = []
    for vm_name in vm_names:
        vm = env.get_vm(vm_name)
        vms.append(vm)

    try:
        option = make_migration_options(method, options, migration_time)

        # make sure cache=none
        if "unsafe" not in options:
            device_target = params.get("virsh_device_target", "sda")
            for vm in vms:
                if vm.is_alive():
                    vm.destroy()
            for each_vm in vm_names:
                logging.info("configure cache=none")
                vmxml = vm_xml.VMXML.new_from_dumpxml(each_vm)
                device_source = str(vmxml.get_disk_attr(each_vm, device_target,
                                                        'source', 'file'))
                ret_detach = virsh.detach_disk(each_vm, device_target,
                                               "--config")
                status = ret_detach.exit_status
                output = ret_detach.stdout.strip()
                logging.info("Status:%s", status)
                logging.info("Output:\n%s", output)
                if not ret_detach:
                    raise exceptions.TestError("Detach disks fails")

                subdriver = utils_test.get_image_info(device_source)['format']
                ret_attach = virsh.attach_disk(each_vm, device_source,
                                               device_target, "--driver qemu "
                                               "--config --cache none "
                                               "--subdriver %s" % subdriver)
                status = ret_attach.exit_status
                output = ret_attach.stdout.strip()
                logging.info("Status:%s", status)
                logging.info("Output:\n%s", output)
                if not ret_attach:
                    raise exceptions.TestError("Attach disks fails")

        for vm in vms:
            if vm.is_dead():
                vm.start()
                vm.wait_for_login()
        multi_migration(vms, srcuri, desturi, option, migration_type,
                        migrate_timeout, jobabort, lrunner=localrunner,
                        rrunner=remoterunner, status_error=status_error)
    except Exception as info:
        logging.error("Test failed: %s" % info)
        flag_migration = False

    # NFS cleanup
    if nfs_shared_disk:
        logging.info("NFS cleanup")
        nfs_client.cleanup(ssh_auto_recover=False)

    localrunner.session.close()
    remoterunner.session.close()

    if not (ret_migration or flag_migration):
        if not status_error:
            raise exceptions.TestFail("Migration test failed")
    if not ret_jobabort:
        if not status_error:
            raise exceptions.TestFail("Abort migration failed")
    if not ret_downtime_tolerable:
        raise exceptions.TestFail("Downtime during migration is intolerable")
