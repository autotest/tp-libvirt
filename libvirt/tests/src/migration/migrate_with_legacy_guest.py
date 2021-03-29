import os
import logging
import re

from avocado.utils import download

from virttest import libvirt_vm
from virttest import defaults
from virttest import virsh
from virttest import remote
from virttest import utils_conn
from virttest import migration
from virttest import libvirt_version

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_config


def run(test, params, env):
    """
    Test virsh migrate command.
    """
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

    migrate_vm_back = "yes" == params.get("migrate_vm_back", "no")
    status_error = "yes" == params.get("status_error", "no")
    remote_virsh_dargs = {'remote_ip': server_ip, 'remote_user': server_user,
                          'remote_pwd': server_pwd, 'unprivileged_user': None,
                          'ssh_remote_auth': True}
    remote_dargs = {'server_ip': server_ip, 'server_user': server_user,
                    'server_pwd': server_pwd,
                    'file_path': "/etc/libvirt/libvirt.conf"}

    xml_check_after_mig = params.get("guest_xml_check_after_mig")

    err_msg = params.get("err_msg")
    vm_session = None
    remote_virsh_session = None
    vm = None
    mig_result = None
    remove_dict = {}
    remote_libvirt_file = None
    src_libvirt_file = None

    if not libvirt_version.version_compare(5, 0, 0):
        test.cancel("This libvirt version doesn't support "
                    "virtio-transitional model.")

    # params for migration connection
    params["virsh_migrate_desturi"] = libvirt_vm.complete_uri(
                                       params.get("migrate_dest_host"))
    params["virsh_migrate_connect_uri"] = libvirt_vm.complete_uri(
                                       params.get("migrate_source_host"))
    src_uri = params.get("virsh_migrate_connect_uri")
    dest_uri = params.get("virsh_migrate_desturi")

    extra_args = migration_test.update_virsh_migrate_extra_args(params)

    vm_name = params.get("migrate_main_vm")
    vm = env.get_vm(vm_name)
    vm.verify_alive()

    # For safety reasons, we'd better back up  xmlfile.
    new_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    orig_config_xml = new_xml.copy()

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
            dom_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
            libvirt.update_memballoon_xml(dom_xml, membal_dict)

        if check_rng:
            rng_dict = {'rng_model': rng_model}
            rng_xml = libvirt.create_rng_xml(rng_dict)
            dom_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
            libvirt.add_vm_device(dom_xml, rng_xml)

        # Change the disk of the vm
        libvirt.set_vm_disk(vm, params)

        if not vm.is_alive():
            vm.start()

        logging.debug("Guest xml after starting:\n%s",
                      vm_xml.VMXML.new_from_dumpxml(vm_name))

        # Check local guest network connection before migration
        vm_session = vm.wait_for_login(restart_network=True)
        migration_test.ping_vm(vm, params)

        remove_dict = {"do_search": '{"%s": "ssh:/"}' % dest_uri}
        src_libvirt_file = libvirt_config.remove_key_for_modular_daemon(
            remove_dict)

        # Execute migration process
        vms = [vm]
        migration_test.do_migration(vms, None, dest_uri, 'orderly',
                                    options, thread_timeout=900,
                                    ignore_status=True, virsh_opt=virsh_options,
                                    extra_opts=extra, **extra_args)
        mig_result = migration_test.ret

        if int(mig_result.exit_status) == 0:
            migration_test.ping_vm(vm, params, dest_uri)

        if xml_check_after_mig:
            if not remote_virsh_session:
                remote_virsh_session = virsh.VirshPersistent(**remote_virsh_dargs)
            target_guest_dumpxml = (remote_virsh_session.dumpxml(vm_name,
                                                                 debug=True,
                                                                 ignore_status=True)
                                    .stdout_text.strip())
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
        if migrate_vm_back:
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
            remove_dict = {"do_search": ('{"%s": "ssh:/"}' % src_uri)}
            remote_libvirt_file = libvirt_config\
                .remove_key_for_modular_daemon(remove_dict, remote_dargs)

            cmd = "virsh migrate %s %s %s" % (vm_name,
                                              options, src_uri)
            logging.debug("Start migration: %s", cmd)
            cmd_result = remote.run_remote_cmd(cmd, params, runner_on_target)
            logging.info(cmd_result)
            if cmd_result.exit_status:
                test.fail("Failed to run '%s' on remote: %s"
                          % (cmd, cmd_result))

    finally:
        logging.debug("Recover test environment")
        # Clean VM on destination
        migration_test.cleanup_vm(vm, dest_uri)

        logging.info("Recover VM XML configration")
        orig_config_xml.sync()
        logging.debug("The current VM XML:\n%s", orig_config_xml.xmltreefile)

        if src_libvirt_file:
            src_libvirt_file.restore()
        if remote_libvirt_file:
            del remote_libvirt_file

        # Clean up of pre migration setup for local machine
        if migrate_vm_back:
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
