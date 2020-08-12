import os
import logging

from virttest import libvirt_vm
from virttest import defaults
from virttest import virsh
from virttest import migration
from virttest import remote
from virttest import utils_net
from virttest import utils_conn
from virttest import virt_vm

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_network
from virttest.libvirt_xml.devices import interface


def run(test, params, env):
    """
    Test migration with special network settings
    1) migrate guest with bridge type interface connected to ovs bridge

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    def check_vm_network_accessed(ping_dest, session=None):
        """
        The operations to the VM need to be done before or after
        migration happens

        :param ping_dest: The destination to be ping
        :param session: The session object to the host
        :raise: test.fail when ping fails
        """
        # Confirm local/remote VM can be accessed through network.
        logging.info("Check VM network connectivity")
        status, output = utils_net.ping(ping_dest,
                                        count=10,
                                        timeout=20,
                                        output_func=logging.debug,
                                        session=session)
        if status != 0:
            test.fail("Ping failed, status: %s, output: %s" % (status, output))

    def update_iface_xml(vm_name, iface_dict):
        """
        Update interfaces for guest

        :param vm_name: The name of VM
        :param iface_dict: The interface configurations params
        """
        logging.debug("update iface xml")
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        vmxml.remove_all_device_by_type('interface')
        vmxml.sync()

        iface = interface.Interface('network')
        iface.xml = libvirt.modify_vm_iface(vm.name, "get_xml", iface_dict)
        libvirt.add_vm_device(vmxml, iface)

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
    server_ip = params.get("server_ip")
    server_user = params.get("server_user", "root")
    server_pwd = params.get("server_pwd")
    client_ip = params.get("client_ip")
    client_pwd = params.get("client_pwd")
    virsh_options = params.get("virsh_options", "")
    extra = params.get("virsh_migrate_extra")
    options = params.get("virsh_migrate_options", "--live --p2p --verbose")
    func_params_exists = "yes" == params.get("func_params_exists", "no")
    migr_vm_back = "yes" == params.get("migr_vm_back", "no")

    ovs_bridge_name = params.get("ovs_bridge_name")
    network_dict = eval(params.get("network_dict", '{}'))
    iface_dict = eval(params.get("iface_dict", '{}'))
    remote_virsh_dargs = {'remote_ip': server_ip, 'remote_user': server_user,
                          'remote_pwd': server_pwd, 'unprivileged_user': None,
                          'ssh_remote_auth': True}
    func_name = None
    libvirtd_conf = None
    mig_result = None

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

    extra_args = {}
    if func_params_exists:
        extra_args.update({'func_params': params})
    postcopy_options = params.get("postcopy_options")
    if postcopy_options:
        extra = "%s %s" % (extra, postcopy_options)
        func_name = virsh.migrate_postcopy

    # For safety reasons, we'd better back up  xmlfile.
    new_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    orig_config_xml = new_xml.copy()

    try:
        # Create a remote runner for later use
        runner_on_target = remote.RemoteRunner(host=server_ip,
                                               username=server_user,
                                               password=server_pwd)
        remote_session = remote.remote_login("ssh", server_ip, "22",
                                             server_user, server_pwd,
                                             r'[$#%]')
        if ovs_bridge_name:
            status, stdout = utils_net.create_ovs_bridge(ovs_bridge_name)
            if status:
                test.fail("Failed to create ovs bridge on local. Status: %s"
                          "Stdout: %s" % (status, stdout))
            status, stdout = utils_net.create_ovs_bridge(
                ovs_bridge_name, session=remote_session)
            if status:
                test.fail("Failed to create ovs bridge on remote. Status: %s"
                          "Stdout: %s" % (status, stdout))
        if network_dict:
            libvirt_network.create_or_del_network(
                network_dict, remote_args=remote_virsh_dargs)
            libvirt_network.create_or_del_network(network_dict)

        remote_session.close()
        # Change domain network xml
        if iface_dict:
            if "mac" not in iface_dict:
                mac = utils_net.generate_mac_address_simple()
                iface_dict.update({'mac': mac})
            else:
                mac = iface_dict["mac"]

            update_iface_xml(vm_name, iface_dict)

        # Change the disk of the vm
        libvirt.set_vm_disk(vm, params)

        if not vm.is_alive():
            try:
                vm.start()
            except virt_vm.VMStartError as err:
                test.fail("Failed to start VM: %s" % err)

        logging.debug("Guest xml after starting:\n%s",
                      vm_xml.VMXML.new_from_dumpxml(vm_name))

        # Check local guest network connection before migration
        if vm.serial_console is not None:
            vm.cleanup_serial_console()
        vm.create_serial_console()
        vm_session = vm.wait_for_serial_login(timeout=240)
        utils_net.restart_guest_network(vm_session)
        vm_ip = utils_net.get_guest_ip_addr(vm_session, mac)
        logging.debug("VM IP Addr: %s", vm_ip)

        check_vm_network_accessed(vm_ip)

        # Execute migration process
        vms = [vm]

        migration_test.do_migration(vms, None, dest_uri, 'orderly',
                                    options, thread_timeout=900,
                                    ignore_status=True, virsh_opt=virsh_options,
                                    func=func_name, extra_opts=extra,
                                    **extra_args)

        mig_result = migration_test.ret
        migration_test.check_result(mig_result, params)

        if int(mig_result.exit_status) == 0:
            remote_session = remote.remote_login("ssh", server_ip, "22",
                                                 server_user, server_pwd,
                                                 r'[$#%]')
            check_vm_network_accessed(vm_ip, session=remote_session)
            remote_session.close()

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

            cmd = "virsh migrate %s %s %s" % (vm_name, options, src_uri)
            logging.debug("Start migration: %s", cmd)
            cmd_result = remote.run_remote_cmd(cmd, params, runner_on_target)
            logging.info(cmd_result)
            if cmd_result.exit_status:
                test.fail("Failed to run '%s' on remote: %s" % (cmd, cmd_result))
            logging.debug("VM is migrated back.")
            check_vm_network_accessed(vm_ip)
    finally:
        logging.debug("Recover test environment")
        # Clean VM on destination and source
        try:
            migration_test.cleanup_dest_vm(vm, vm.connect_uri, dest_uri)
        except Exception as err:
            logging.error(err)
        if vm.is_alive():
            vm.destroy(gracefully=False)

        logging.info("Recovery VM XML configration")
        orig_config_xml.sync()
        remote_session = remote.remote_login("ssh", server_ip, "22",
                                             server_user, server_pwd,
                                             r'[$#%]')
        if network_dict:
            libvirt_network.create_or_del_network(
                network_dict, is_del=True, remote_args=remote_virsh_dargs)
            libvirt_network.create_or_del_network(network_dict, is_del=True)
        if ovs_bridge_name:
            utils_net.delete_ovs_bridge(ovs_bridge_name)
            utils_net.delete_ovs_bridge(ovs_bridge_name, session=remote_session)

        remote_session.close()
        if migr_vm_back:
            if 'ssh_connection' in locals():
                ssh_connection.auto_recover = True
            migration_test.migrate_pre_setup(src_uri, params,
                                             cleanup=True)
        logging.info("Remove local NFS image")
        source_file = params.get("source_file")
        if source_file:
            libvirt.delete_local_disk("file", path=source_file)
