import logging

from virttest import libvirt_version
from virttest import libvirt_vm
from virttest import migration
from virttest import remote
from virttest import utils_net
from virttest import utils_conn
from virttest import utils_package
from virttest import virsh
from virttest import virt_vm

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_network
from virttest.libvirt_xml.devices import interface


def run(test, params, env):
    """
    Test migration with special network settings
    1) migrate guest with bridge type interface connected to ovs bridge
    2) migrate guest with direct type interface when a macvtap device name
        exists on dest host

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

    def vm_sync(vmxml, vm_name=None, virsh_instance=virsh):
        """
        A wrapper to sync vm xml on localhost and remote host

        :param vmxml: domain VMXML instance
        :param vm_name: The name of VM
        :param virsh_instance: virsh instance object
        """
        if vm_name and virsh_instance != virsh:
            remote.scp_to_remote(server_ip, '22', server_user,
                                 server_pwd,
                                 vmxml.xml, vmxml.xml)
            if virsh_instance.domain_exists(vm_name):
                if virsh_instance.is_alive(vm_name):
                    virsh_instance.destroy(vm_name, ignore_status=True)
                virsh_instance.undefine(vmxml.xml, ignore_status=True)
            virsh_instance.define(vmxml.xml, debug=True)
        else:
            vmxml.sync()

    def update_iface_xml(vm_name, iface_dict, virsh_instance=virsh):
        """
        Update interfaces for guest

        :param vm_name: The name of VM
        :param iface_dict: The interface configurations params
        :param virsh_instance: virsh instance object
        """
        logging.debug("update iface xml")
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(
            vm_name, virsh_instance=virsh_instance)
        vmxml.remove_all_device_by_type('interface')
        vm_sync(vmxml, vm_name, virsh_instance=virsh_instance)
        iface = interface.Interface('network')
        iface.xml = libvirt.modify_vm_iface(vm_name, "get_xml", iface_dict,
                                            virsh_instance=virsh_instance)
        vmxml.add_device(iface)
        vmxml.xmltreefile.write()
        vm_sync(vmxml, vm_name, virsh_instance=virsh_instance)
        logging.debug("VM XML after updating interface: %s" % vmxml)

    def update_net_dict(net_dict, runner=utils_net.local_runner):
        """
        Update network dict

        :param net_dict: The network dict to be updated
        :param runner: Command runner
        :return: Updated network dict
        """
        if net_dict.get("net_name", "") == "direct-macvtap":
            logging.info("Updating network iface name")
            iface_name = utils_net.get_net_if(runner=runner, state="UP")[0]
            net_dict.update({"forward_iface": iface_name})
        else:
            # TODO: support other types
            logging.info("No need to update net_dict. We only support to "
                         "update direct-macvtap type for now.")
        logging.debug("net_dict is %s" % net_dict)
        return net_dict

    def get_remote_direct_mode_vm_mac(vm_name, uri):
        """
        Get mac of remote direct mode VM

        :param vm_name: The name of VM
        :param uri: The uri on destination
        :return: mac
        :raise: test.fail when the result of virsh domiflist is incorrect
        """
        vm_mac = None
        res = virsh.domiflist(
            vm_name, uri=uri, ignore_status=False).stdout_text.strip().split("\n")
        if len(res) < 2:
            test.fail("Unable to get remote VM's mac: %s" % res)
        else:
            vm_mac = res[-1].split()[-1]
        return vm_mac

    def create_fake_tap(remote_session):
        """
        Create a fake macvtap on destination host.

        :param remote_session: The session to the destination host.
        :return: The new tap device
        """
        tap_cmd = "ls /dev/tap* |awk -F 'tap' '{print $NF}'"
        tap_idx = remote_session.cmd_output(tap_cmd).strip()
        if not tap_idx:
            test.fail("Unable to get tap index using %s."
                      % tap_cmd)
        fake_tap_dest = 'tap'+str(int(tap_idx)+1)
        logging.debug("creating a fake tap %s...", fake_tap_dest)
        cmd = "touch /dev/%s" % fake_tap_dest
        remote_session.cmd(cmd)
        return fake_tap_dest

    migration_test = migration.MigrationTest()
    migration_test.check_parameters(params)

    libvirt_version.is_libvirt_feature_supported(params)

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
    restart_dhclient = params.get("restart_dhclient", "dhclient -r; dhclient")
    ping_dest = params.get("ping_dest", "www.baidu.com")
    extra_args = migration_test.update_virsh_migrate_extra_args(params)

    migrate_vm_back = "yes" == params.get("migrate_vm_back", "no")

    target_vm_name = params.get("target_vm_name")
    direct_mode = "yes" == params.get("direct_mode", "no")
    check_macvtap_exists = "yes" == params.get("check_macvtap_exists", "no")
    create_fake_tap_dest = "yes" == params.get("create_fake_tap_dest", "no")
    macvtap_cmd = params.get("macvtap_cmd")
    modify_target_vm = "yes" == params.get("modify_target_vm", "no")
    ovs_bridge_name = params.get("ovs_bridge_name")
    network_dict = eval(params.get("network_dict", '{}'))
    iface_dict = eval(params.get("iface_dict", '{}'))
    remote_virsh_dargs = {'remote_ip': server_ip, 'remote_user': server_user,
                          'remote_pwd': server_pwd, 'unprivileged_user': None,
                          'ssh_remote_auth': True}
    cmd_parms = {'server_ip': server_ip, 'server_user': server_user,
                 'server_pwd': server_pwd}

    virsh_session_remote = None
    libvirtd_conf = None
    mig_result = None
    target_org_xml = None
    target_vm_session = None
    target_vm = None
    exp_macvtap = []
    fake_tap_dest = None

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
    bk_uri = vm.connect_uri

    postcopy_options = params.get("postcopy_options")
    action_during_mig = None
    if postcopy_options:
        extra = "%s %s" % (extra, postcopy_options)
        action_during_mig = virsh.migrate_postcopy

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
        virsh_session_remote = virsh.VirshPersistent(**remote_virsh_dargs)

        if target_vm_name:
            target_vm = libvirt_vm.VM(target_vm_name, params, vm.root_dir,
                                      vm.address_cache)
            target_vm.connect_uri = dest_uri
            if not virsh_session_remote.domain_exists(target_vm_name):
                test.error("VM %s should be installed on %s."
                           % (target_vm_name, server_ip))
            # Backup guest's xml on remote
            target_org_xml = vm_xml.VMXML.new_from_inactive_dumpxml(
                target_vm_name, virsh_instance=virsh_session_remote)
            # Scp original xml to remote for restoration
            remote.scp_to_remote(server_ip, '22', server_user,
                                 server_pwd,
                                 target_org_xml.xml, target_org_xml.xml)
            logging.debug("target xml is %s" % target_org_xml)

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
            update_net_dict(network_dict, runner=remote_session.cmd)
            libvirt_network.create_or_del_network(
                network_dict, remote_args=remote_virsh_dargs)
            logging.info("dest: network created")
            update_net_dict(network_dict)
            libvirt_network.create_or_del_network(network_dict)
            logging.info("localhost: network created")

        if target_vm_name:
            if modify_target_vm and iface_dict:
                logging.info("Updating remote VM's interface")
                update_iface_xml(target_vm_name, iface_dict,
                                 virsh_instance=virsh_session_remote)
            target_vm.start()
            target_vm_session = target_vm.wait_for_serial_login(timeout=240)
            check_vm_network_accessed(ping_dest, session=target_vm_session)
            if check_macvtap_exists and macvtap_cmd:
                # Get macvtap device's index on remote after target_vm started
                idx = remote_session.cmd_output(macvtap_cmd).strip()
                if not idx:
                    test.fail("Unable to get macvtap index using %s."
                              % macvtap_cmd)
                # Generate the expected macvtap devices' index list
                exp_macvtap = ['macvtap'+idx, 'macvtap'+str(int(idx)+1)]
                if create_fake_tap_dest:
                    fake_tap_dest = create_fake_tap(remote_session)

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
        if not utils_package.package_install('dhcp-client', session=vm_session):
            test.error("Failed to install dhcp-client on guest.")
        utils_net.restart_guest_network(vm_session)
        vm_ip = utils_net.get_guest_ip_addr(vm_session, mac)
        logging.debug("VM IP Addr: %s", vm_ip)

        if direct_mode:
            check_vm_network_accessed(ping_dest, session=vm_session)
        else:
            check_vm_network_accessed(vm_ip)

        # Execute migration process
        vms = [vm]

        migration_test.do_migration(vms, None, dest_uri, 'orderly',
                                    options, thread_timeout=900,
                                    ignore_status=True, virsh_opt=virsh_options,
                                    func=action_during_mig,
                                    extra_opts=extra,
                                    **extra_args)

        mig_result = migration_test.ret

        # Check network accessibility after migration
        if int(mig_result.exit_status) == 0:
            vm.connect_uri = dest_uri
            if vm.serial_console is not None:
                vm.cleanup_serial_console()
            vm.create_serial_console()
            vm_session_after_mig = vm.wait_for_serial_login(timeout=240)
            vm_session_after_mig.cmd(restart_dhclient)
            check_vm_network_accessed(ping_dest, session=vm_session_after_mig)

            if check_macvtap_exists and macvtap_cmd:
                remote_session = remote.remote_login("ssh", server_ip, "22",
                                                     server_user, server_pwd,
                                                     r'[$#%]')
                # Check macvtap devices' index after migration
                idx = remote_session.cmd_output(macvtap_cmd)
                act_macvtap = ['macvtap'+i for i in idx.strip().split("\n")]
                if act_macvtap != exp_macvtap:
                    test.fail("macvtap devices after migration are incorrect!"
                              " Actual: %s, Expected: %s. "
                              % (act_macvtap, exp_macvtap))
        else:
            if fake_tap_dest:
                res = remote.run_remote_cmd("ls /dev/%s" % fake_tap_dest,
                                            params, runner_on_target)
                libvirt.check_exit_status(res)

        if target_vm_session:
            check_vm_network_accessed(ping_dest, session=target_vm_session)
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

            cmd = "virsh migrate %s %s %s" % (vm_name, options, src_uri)
            logging.debug("Start migration: %s", cmd)
            cmd_result = remote.run_remote_cmd(cmd, params, runner_on_target)
            logging.info(cmd_result)
            if cmd_result.exit_status:
                test.fail("Failed to run '%s' on remote: %s" % (cmd, cmd_result))
            logging.debug("VM is migrated back.")

            vm.connect_uri = bk_uri
            if vm.serial_console is not None:
                vm.cleanup_serial_console()
            vm.create_serial_console()
            vm_session_after_mig_bak = vm.wait_for_serial_login(timeout=240)
            vm_session_after_mig_bak.cmd(restart_dhclient)
            check_vm_network_accessed(ping_dest, vm_session_after_mig_bak)
    finally:
        logging.debug("Recover test environment")
        vm.connect_uri = bk_uri
        migration_test.cleanup_vm(vm, dest_uri)

        logging.info("Recovery VM XML configration")
        orig_config_xml.sync()
        remote_session = remote.remote_login("ssh", server_ip, "22",
                                             server_user, server_pwd,
                                             r'[$#%]')
        if target_vm and target_vm.is_alive():
            target_vm.destroy(gracefully=False)

        if target_org_xml and target_vm_name:
            logging.info("Recovery XML configration for %s.", target_vm_name)
            virsh_session_remote = virsh.VirshPersistent(**remote_virsh_dargs)
            vm_sync(target_org_xml, vm_name=target_vm_name,
                    virsh_instance=virsh_session_remote)
            virsh_session_remote.close_session()

        if fake_tap_dest:
            remote_session.cmd_output_safe("rm -rf /dev/%s" % fake_tap_dest)

        if network_dict:
            libvirt_network.create_or_del_network(
                network_dict, is_del=True, remote_args=remote_virsh_dargs)
            libvirt_network.create_or_del_network(network_dict, is_del=True)
        if ovs_bridge_name:
            utils_net.delete_ovs_bridge(ovs_bridge_name)
            utils_net.delete_ovs_bridge(ovs_bridge_name, session=remote_session)

        remote_session.close()
        if target_vm_session:
            target_vm_session.close()

        if virsh_session_remote:
            virsh_session_remote.close_session()

        if migrate_vm_back:
            if 'ssh_connection' in locals():
                ssh_connection.auto_recover = True
            migration_test.migrate_pre_setup(src_uri, params,
                                             cleanup=True)
        logging.info("Remove local NFS image")
        source_file = params.get("source_file")
        if source_file:
            libvirt.delete_local_disk("file", path=source_file)
