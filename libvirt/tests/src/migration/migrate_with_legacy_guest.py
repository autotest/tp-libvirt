import os
import logging
import re

from avocado.utils import download

from virttest import libvirt_vm
from virttest import utils_test
from virttest import defaults
from virttest import virsh
from virttest import remote
from virttest import utils_conn
from virttest import libvirt_version

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.compat_52lts import results_stdout_52lts
from virttest.compat_52lts import results_stderr_52lts


def check_parameters(test, params):
    """
    Make sure all of parameters are assigned a valid value

    :param test: the test object
    :param params: the parameters to be checked

    :raise: test.cancel if invalid value exists
    """
    migrate_dest_host = params.get("migrate_dest_host")
    migrate_dest_pwd = params.get("migrate_dest_pwd")
    migrate_source_host = params.get("migrate_source_host")
    migrate_source_pwd = params.get("migrate_source_pwd")

    args_list = [migrate_dest_host,
                 migrate_dest_pwd, migrate_source_host,
                 migrate_source_pwd]

    for arg in args_list:
        if arg and arg.count("EXAMPLE"):
            test.cancel("Please assign a value for %s!" % arg)


def run(test, params, env):
    """
    Test virsh migrate command.
    """

    def check_vm_network_accessed(session=None):
        """
        The operations to the VM need to be done before or after
        migration happens

        :param session: The session object to the host

        :raise: test.error when ping fails
        """
        # Confirm local/remote VM can be accessed through network.
        logging.info("Check VM network connectivity")
        s_ping, _ = utils_test.ping(vm.get_address(),
                                    count=10,
                                    timeout=20,
                                    output_func=logging.debug,
                                    session=session)
        if s_ping != 0:
            if session:
                session.close()
            test.fail("%s did not respond after %d sec." % (vm.name, 20))

    def check_migration_res(result):
        """
        Check if the migration result is as expected

        :param result: the output of migration
        :raise: test.fail if test is failed
        """
        if not result:
            test.error("No migration result is returned.")

        logging.info("Migration out: %s", results_stdout_52lts(result).strip())
        logging.info("Migration error: %s", results_stderr_52lts(result).strip())

        if status_error:  # Migration should fail
            if err_msg:   # Special error messages are expected
                if not re.search(err_msg, results_stderr_52lts(result).strip()):
                    test.fail("Can not find the expected patterns '%s' in "
                              "output '%s'" % (err_msg,
                                               results_stderr_52lts(result).strip()))
                else:
                    logging.debug("It is the expected error message")
            else:
                if int(result.exit_status) != 0:
                    logging.debug("Migration failure is expected result")
                else:
                    test.fail("Migration success is unexpected result")
        else:
            if int(result.exit_status) != 0:
                test.fail(results_stderr_52lts(result).strip())

    check_parameters(test, params)

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
    virsh_args = {"debug": True}
    virsh_options = params.get("virsh_options", "")

    server_ip = params.get("server_ip")
    server_user = params.get("server_user", "root")
    server_pwd = params.get("server_pwd")
    client_ip = params.get("client_ip")
    client_pwd = params.get("client_pwd")
    extra = params.get("virsh_migrate_extra")
    options = params.get("virsh_migrate_options")

    guest_src_url = params.get("guest_src_url")
    guest_src_path = params.get("guest_src_path",
                                "/var/lib/libvirt/images/guest.img")
    check_disk = "yes" == params.get("check_disk")
    disk_model = params.get("disk_model")
    disk_target = params.get("disk_target", "vda")
    controller_model = params.get("controller_model")

    check_interface = "yes" == params.get("check_interface")
    iface_type = params.get("iface_type", "network")
    iface_model = params.get("iface_model", "virtio")
    iface_params = {'type': iface_type,
                    'model': iface_model,
                    'del_addr': True,
                    'source': '{"network": "default"}'}

    check_memballoon = "yes" == params.get("check_memballoon")
    membal_model = params.get("membal_model")

    check_rng = "yes" == params.get("check_rng")
    rng_model = params.get("rng_model")

    migr_vm_back = "yes" == params.get("migrate_vm_back", "no")
    status_error = "yes" == params.get("status_error", "no")
    remote_virsh_dargs = {'remote_ip': server_ip, 'remote_user': server_user,
                          'remote_pwd': server_pwd, 'unprivileged_user': None,
                          'ssh_remote_auth': True}

    xml_check_after_mig = params.get("guest_xml_check_after_mig")

    err_msg = params.get("err_msg")
    vm_session = None
    remote_virsh_session = None
    vm = None
    mig_result = None

    if not libvirt_version.version_compare(5, 0, 0):
        test.cancel("This libvirt version doesn't support "
                    "virtio-transitional model.")
    # Make sure all of parameters are assigned a valid value
    check_parameters(test, params)

    # params for migration connection
    params["virsh_migrate_desturi"] = libvirt_vm.complete_uri(
                                       params.get("migrate_dest_host"))
    params["virsh_migrate_connect_uri"] = libvirt_vm.complete_uri(
                                       params.get("migrate_source_host"))
    src_uri = params.get("virsh_migrate_connect_uri")
    dest_uri = params.get("virsh_migrate_desturi")

    vm_name = params.get("migrate_main_vm")
    vm = env.get_vm(vm_name)
    vm.verify_alive()

    # For safety reasons, we'd better back up  xmlfile.
    new_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    orig_config_xml = new_xml.copy()

    migration_test = libvirt.MigrationTest()
    try:
        # Create a remote runner for later use
        runner_on_target = remote.RemoteRunner(host=server_ip,
                                               username=server_user,
                                               password=server_pwd)
        # download guest source and update interface model to keep guest up
        if guest_src_url:
            blk_source = download.get_file(guest_src_url, guest_src_path)
            if not blk_source:
                test.error("Fail to download image.")
            params["blk_source_name"] = blk_source
            if (not check_interface) and iface_model:
                iface_dict = {'model': iface_model}
                libvirt.modify_vm_iface(vm_name, "update_iface", iface_dict)
            if not check_disk:
                params["disk_model"] = "virtio-transitional"

        if check_interface:
            libvirt.modify_vm_iface(vm_name, "update_iface", iface_params)

        if check_memballoon:
            membal_dict = {'membal_model': membal_model}
            libvirt.update_memballoon_xml(new_xml, membal_dict)

        if check_rng:
            rng_dict = {'rng_model': rng_model}
            rng_xml = libvirt.create_rng_xml(rng_dict)
            libvirt.add_vm_device(new_xml, rng_xml)

        # Change the disk of the vm
        libvirt.set_vm_disk(vm, params)

        if not vm.is_alive():
            vm.start()

        logging.debug("Guest xml after starting:\n%s",
                      vm_xml.VMXML.new_from_dumpxml(vm_name))

        # Check local guest network connection before migration
        vm_session = vm.wait_for_login(restart_network=True)
        check_vm_network_accessed()

        # Execute migration process
        vms = [vm]
        migration_test.do_migration(vms, None, dest_uri, 'orderly',
                                    options, thread_timeout=900,
                                    ignore_status=True, virsh_opt=virsh_options,
                                    extra_opts=extra)
        mig_result = migration_test.ret

        check_migration_res(mig_result)

        if int(mig_result.exit_status) == 0:
            server_session = remote.wait_for_login('ssh', server_ip, '22',
                                                   server_user, server_pwd,
                                                   r"[\#\$]\s*$")
            check_vm_network_accessed(server_session)
            server_session.close()

        if xml_check_after_mig:
            if not remote_virsh_session:
                remote_virsh_session = virsh.VirshPersistent(**remote_virsh_dargs)
            target_guest_dumpxml = results_stdout_52lts(
                remote_virsh_session.dumpxml(vm_name,
                                             debug=True,
                                             ignore_status=True)).strip()
            if check_disk:
                check_str = disk_model if disk_model else controller_model
            if check_interface:
                check_str = iface_model
            if check_memballoon:
                check_str = membal_model
            if check_rng:
                check_str = rng_model

            xml_check_after_mig = "%s'%s'" % (xml_check_after_mig, check_str)
            if not re.search(xml_check_after_mig, target_guest_dumpxml):
                test.fail("Fail to search '%s' in target guest XML:\n%s"
                          % (xml_check_after_mig, target_guest_dumpxml))
            remote_virsh_session.close_session()

        # Execute migration from remote
        if migr_vm_back:
            ssh_connection = utils_conn.SSHConnection(server_ip=client_ip,
                                                      server_pwd=client_pwd,
                                                      client_ip=server_ip,
                                                      client_pwd=server_pwd)
            try:
                ssh_connection.conn_check()
            except utils_conn.ConnectionError:
                ssh_connection.conn_setup()
                ssh_connection.conn_check()

            # Pre migration setup for local machine
            migration_test.migrate_pre_setup(src_uri, params)
            cmd = "virsh migrate %s %s %s" % (vm_name,
                                              virsh_options, src_uri)
            logging.debug("Start migration: %s", cmd)
            cmd_result = remote.run_remote_cmd(cmd, params, runner_on_target)
            logging.info(cmd_result)
            if cmd_result.exit_status:
                test.fail("Failed to run '%s' on remote: %s"
                          % (cmd, cmd_result))

    finally:
        logging.debug("Recover test environment")
        # Clean VM on destination
        vm.connect_uri = ''
        migration_test.cleanup_dest_vm(vm, src_uri, dest_uri)

        logging.info("Recovery VM XML configration")
        orig_config_xml.sync()
        logging.debug("The current VM XML:\n%s", orig_config_xml.xmltreefile)

        # Clean up of pre migration setup for local machine
        if migr_vm_back:
            if 'ssh_connection' in locals():
                ssh_connection.auto_recover = True
            migration_test.migrate_pre_setup(src_uri, params,
                                             cleanup=True)
        if remote_virsh_session:
            remote_virsh_session.close_session()

        logging.info("Remove local NFS image")
        source_file = params.get("source_file")
        libvirt.delete_local_disk("file", path=source_file)
        if guest_src_url and blk_source:
            libvirt.delete_local_disk("file", path=blk_source)
