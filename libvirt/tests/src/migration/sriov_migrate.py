import os
import copy
import logging
import re

from avocado.utils import process

from virttest import defaults
from virttest import libvirt_version
from virttest import libvirt_vm
from virttest import migration
from virttest import remote
from virttest import utils_conn
from virttest import utils_misc
from virttest import utils_net
from virttest import utils_sriov
from virttest import utils_test
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices import interface
from virttest.utils_libvirt import libvirt_config
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Test virsh migrate command.
    """

    def check_vm_network_accessed(session=None, ping_dest="8.8.8.8"):
        """
        The operations to the VM need to be done before or after
        migration happens

        :param session: The session object to the host
        :param ping_dest: The destination to be ping

        :raise: test.fail when ping fails
        """
        # Confirm local/remote VM can be accessed through network.
        logging.info("Check VM network connectivity")
        status, output = utils_test.ping(ping_dest,
                                         count=10,
                                         timeout=20,
                                         output_func=logging.debug,
                                         session=session)
        if status != 0:
            test.fail("Ping failed, status: %s,"
                      " output: %s" % (status, output))

    def get_vm_ifaces(session=None):
        """
        Get interfaces of vm

        :param session: The session object to the host
        :return: interfaces
        """
        p_iface, v_iface = utils_net.get_remote_host_net_ifs(session)

        return p_iface

    def check_vm_iface_num(iface_list, exp_num=3):
        """
        Check he number of interfaces

        :param iface_list: The interface list
        :param exp_num: The expected number
        :raise: test.fail when interfaces' number is not equal to exp_num
        """
        if len(iface_list) != exp_num:
            test.fail("%d interfaces should be found on the vm, "
                      "but find %s." % (exp_num, iface_list))

    def create_or_del_networks(pf_name, params, remote_virsh_session=None,
                               is_del=False):
        """
        Create or delete network on local or remote

        :param params: Dictionary with the test parameters
        :param pf_name: The name of PF
        :param remote_virsh_session: The virsh session object to the remote host
        :param is_del: Whether the networks should be deleted
        :raise: test.fail when fails to define/start network
        """
        net_bridge_name = params.get("net_bridge_name", "host-bridge")
        net_bridge_fwd = params.get("net_bridge_fwd", '{"mode": "bridge"}')
        bridge_name = params.get("bridge_name", "br0")

        bridge_dict = {"net_name": net_bridge_name,
                       "net_forward": net_bridge_fwd,
                       "net_bridge": '{"name": "%s"}' % bridge_name}
        net_list = [bridge_dict]
        enable_hostdev_iface = "yes" == params.get("enable_hostdev_iface", "no")
        if enable_hostdev_iface:
            net_hostdev_name = params.get("net_hostdev_name", "hostdev-net")
            net_hostdev_fwd = params.get("net_hostdev_fwd",
                                         '{"mode": "hostdev", "managed": "yes"}')
            net_dict = {"net_name": net_hostdev_name,
                        "net_forward": net_hostdev_fwd,
                        "net_forward_pf": '{"dev": "%s"}' % pf_name}
            net_list.append(net_dict)

        if not is_del:
            for net_params in net_list:
                net_dev = libvirt.create_net_xml(net_params.get("net_name"),
                                                 net_params)
                if not remote_virsh_session:
                    if net_dev.get_active():
                        net_dev.undefine()
                    net_dev.define()
                    net_dev.start()
                else:
                    remote.scp_to_remote(server_ip, '22', server_user, server_pwd,
                                         net_dev.xml, net_dev.xml, limit="",
                                         log_filename=None, timeout=600,
                                         interface=None)
                    remote_virsh_session.net_define(net_dev.xml, **virsh_args)
                    remote_virsh_session.net_start(net_params.get("net_name"),
                                                   **virsh_args)

        else:
            virsh_session = virsh
            if remote_virsh_session:
                virsh_session = remote_virsh_session
            for nname in [n_dict.get("net_name") for n_dict in net_list]:
                if nname not in virsh_session.net_state_dict():
                    continue
                virsh_session.net_destroy(nname, debug=True, ignore_status=True)
                virsh_session.net_undefine(nname, debug=True, ignore_status=True)

    def check_vm_network_connection(net_name, expected_conn=0):
        """
        Check network connections in network xml

        :param net_name: The network to be checked
        :param expected_conn: The expected value
        :raise: test.fail when fails
        """
        output = virsh.net_dumpxml(net_name, debug=True).stdout_text
        if expected_conn == 0:
            reg_pattern = r"<network>"
        else:
            reg_pattern = r"<network connections='(\d)'>"
        res = re.findall(reg_pattern, output, re.I)
        if not res:
            test.fail("Unable to find expected connection in %s." % net_name)
        if expected_conn != 0:
            if expected_conn != int(res[0]):
                test.fail("Unable to get expected connection number."
                          "Expected: %s, Actual %s" % (expected_conn, int(res[0])))

    def get_hostdev_addr_from_xml():
        """
        Get VM hostdev address

        :return: pci driver id
        """
        address_dict = {}
        for ifac in vm_xml.VMXML.new_from_dumpxml(vm_name).devices.by_device_tag("interface"):
            if ifac.type_name == "hostdev":
                address_dict = ifac.hostdev_address.attrs

        return libvirt.pci_info_from_address(address_dict, 16, "id")

    def check_vfio_pci(pci_path, status_error=False):
        """
        Check if vf driver is vfio-pci

        :param pci_path: The absolute path of pci device
        :param status_error: Whether the driver should be vfio-pci
        """
        cmd = "readlink %s/driver | awk -F '/' '{print $NF}'" % pci_path
        output = process.run(cmd, shell=True, verbose=True).stdout_text.strip()
        if (output == "vfio-pci") == status_error:
            test.fail("Get incorrect dirver %s, it should%s be vfio-pci."
                      % (output, ' not' if status_error else ''))

    def update_iface_xml(vmxml, enable_hostdev_iface):
        """
        Update interfaces for guest

        :param vmxml: vm_xml.VMXML object

        """
        vmxml.remove_all_device_by_type('interface')
        vmxml.sync()

        iface_list = []
        iface_dict = {"type": "network", "source": "{'network': 'host-bridge'}",
                      "mac": mac_addr, "model": "virtio",
                      "teaming": '{"type":"persistent"}',
                      "alias": '{"name": "ua-backup0"}',
                      "inbound": '{"average":"5"}',
                      "outbound": '{"average":"5"}'}
        iface_list.append(iface_dict)
        if enable_hostdev_iface:
            iface_dict2 = {"type": "network",
                           "source": "{'network': 'hostdev-net'}",
                           "mac": mac_addr, "model": "virtio",
                           "teaming": '{"type":"transient", \
                                "persistent": "ua-backup0"}'}
            iface_list.append(iface_dict2)

        iface = interface.Interface('network')
        for ifc in iface_list:
            iface.xml = libvirt.modify_vm_iface(vm.name, "get_xml", ifc)
            vmxml.add_device(iface)
        vmxml.sync()

    def add_hostdev_device(vm_name, pf_pci, params):
        """
        Add a hostdev device to the guest

        :param vm_name: VM's name
        :param pf_pci: The PF's pci
        :param params: The parameters used
        """
        vf_pci = utils_sriov.get_vf_pci_id(pf_pci)
        hostdev_teaming_dict = params.get("hostdev_device_teaming_dict", '{}')
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        hostdev_dev = libvirt.create_hostdev_xml(vf_pci,
                                                 teaming=hostdev_teaming_dict)
        vmxml.add_device(hostdev_dev)
        vmxml.sync()

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

    bridge_name = params.get("bridge_name", "br0")
    net_hostdev_name = params.get("net_hostdev_name", "hostdev-net")
    net_bridge_name = params.get("net_bridge_name", "host-bridge")
    enable_hostdev_iface = "yes" == params.get("enable_hostdev_iface", "no")
    enable_hostdev_device = "yes" == params.get("enable_hostdev_device", "no")
    driver = params.get("driver", "ixgbe")
    vm_tmp_file = params.get("vm_tmp_file", "/tmp/test.txt")
    cmd_during_mig = params.get("cmd_during_mig")
    net_failover_test = "yes" == params.get("net_failover_test", "no")
    cancel_migration = "yes" == params.get("cancel_migration", "no")
    try:
        vf_no = int(params.get("vf_no", "4"))
    except ValueError as e:
        test.error(e)

    migr_vm_back = "yes" == params.get("migrate_vm_back", "no")
    err_msg = params.get("err_msg")
    status_error = "yes" == params.get("status_error", "no")
    cmd_parms = {'server_ip': server_ip, 'server_user': server_user,
                 'server_pwd': server_pwd}
    remote_virsh_dargs = {'remote_ip': server_ip, 'remote_user': server_user,
                          'remote_pwd': server_pwd, 'unprivileged_user': None,
                          'ssh_remote_auth': True}
    remote_dargs = {'server_ip': server_ip, 'server_user': server_user,
                    'server_pwd': server_pwd,
                    'file_path': "/etc/libvirt/libvirt.conf"}

    destparams_dict = copy.deepcopy(params)

    remote_virsh_session = None
    vm_session = None
    vm = None
    mig_result = None
    action_during_mig = None
    default_src_vf = 0
    default_dest_vf = 0
    default_src_rp_filter = 1
    default_dest_rp_filer = 1
    remove_dict = {}
    remote_libvirt_file = None
    src_libvirt_file = None

    libvirt_version.is_libvirt_feature_supported(params)

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

    extra_args = migration_test.update_virsh_migrate_extra_args(params)

    # For safety reasons, we'd better back up  xmlfile.
    new_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    orig_config_xml = new_xml.copy()

    try:
        # Create a remote runner for later use
        runner_on_target = remote.RemoteRunner(host=server_ip,
                                               username=server_user,
                                               password=server_pwd)

        server_session = remote.wait_for_login('ssh', server_ip, '22',
                                               server_user, server_pwd,
                                               r"[\#\$]\s*$")
        if net_failover_test:
            src_pf, src_pf_pci = utils_sriov.find_pf(driver)
            logging.debug("src_pf is %s. src_pf_pci: %s", src_pf, src_pf_pci)
            params['pf_name'] = src_pf
            dest_pf, dest_pf_pci = utils_sriov.find_pf(driver, server_session)
            logging.debug("dest_pf is %s. dest_pf_pci: %s", dest_pf, dest_pf_pci)
            destparams_dict['pf_name'] = dest_pf

            src_pf_pci_path = utils_misc.get_pci_path(src_pf_pci)
            dest_pf_pci_path = utils_misc.get_pci_path(dest_pf_pci, server_session)

            cmd = "cat %s/sriov_numvfs" % (src_pf_pci_path)
            default_src_vf = process.run(cmd, shell=True,
                                         verbose=True).stdout_text

            cmd = "cat %s/sriov_numvfs" % (dest_pf_pci_path)
            status, default_dest_vf = utils_misc.cmd_status_output(cmd,
                                                                   shell=True,
                                                                   session=server_session)
            if status:
                test.error("Unable to get default sriov_numvfs on target!"
                           "status: %s, output: %s" % (status, default_dest_vf))

            if not utils_sriov.set_vf(src_pf_pci_path, vf_no):
                test.error("Failed to set vf on source.")

            if not utils_sriov.set_vf(dest_pf_pci_path, vf_no, session=server_session):
                test.error("Failed to set vf on target.")

            # Create PF and bridge connection on source and target host
            cmd = 'cat /proc/sys/net/ipv4/conf/all/rp_filter'
            default_src_rp_filter = process.run(cmd, shell=True,
                                                verbose=True).stdout_text
            status, default_dest_rp_filter = utils_misc.cmd_status_output(cmd,
                                                                          shell=True,
                                                                          session=server_session)
            if status:
                test.error("Unable to get default rp_filter on target!"
                           "status: %s, output: %s" % (status, default_dest_rp_filter))
            cmd = 'echo 0 >/proc/sys/net/ipv4/conf/all/rp_filter'
            process.run(cmd, shell=True, verbose=True)
            utils_misc.cmd_status_output(cmd, shell=True, session=server_session)
            if enable_hostdev_device:
                hostdev_pci_id = utils_sriov.get_vf_pci_id(src_pf_pci).strip()
                dest_vf_pci_id = utils_sriov.get_vf_pci_id(
                    dest_pf_pci, session=server_session).strip()
                logging.debug("src_vf_pci_id:%s", hostdev_pci_id)
                logging.debug("dest_vf_pci_id:%s", dest_vf_pci_id)
                if dest_vf_pci_id != hostdev_pci_id:
                    # TODO: Create migratable xml with the updated vf's pci address
                    test.error("The pci address of vf is different on source "
                               "host and target host.")

            utils_sriov.add_or_del_connection(params, is_del=False)
            utils_sriov.add_or_del_connection(destparams_dict, is_del=False,
                                              session=server_session)

            if not remote_virsh_session:
                remote_virsh_session = virsh.VirshPersistent(**remote_virsh_dargs)
            create_or_del_networks(dest_pf, params,
                                   remote_virsh_session=remote_virsh_session)
            remote_virsh_session.close_session()
            create_or_del_networks(src_pf, params)
            # Change network interface xml
            mac_addr = utils_net.generate_mac_address_simple()
            if enable_hostdev_device:
                utils_sriov.set_vf_mac(src_pf, mac_addr)
                utils_sriov.set_vf_mac(dest_pf, mac_addr, session=server_session)

            update_iface_xml(new_xml, enable_hostdev_iface)
            if enable_hostdev_device:
                add_hostdev_device(vm_name, src_pf_pci, params)

        # Change the disk of the vm
        libvirt.set_vm_disk(vm, params)

        if not vm.is_alive():
            vm.start()

        # Check local guest network connection before migration
        if vm.serial_console is not None:
            vm.cleanup_serial_console()
        vm.create_serial_console()
        vm_session = vm.wait_for_serial_login(timeout=240)

        if net_failover_test:
            utils_net.restart_guest_network(vm_session)
        iface_list = get_vm_ifaces(vm_session)

        vm_ipv4, vm_ipv6 = utils_net.get_linux_ipaddr(vm_session, iface_list[0])
        check_vm_network_accessed(ping_dest=vm_ipv4)

        if net_failover_test:
            check_vm_iface_num(iface_list)
            if enable_hostdev_iface:
                check_vm_network_connection(net_hostdev_name, 1)
            check_vm_network_connection(net_bridge_name, 1)

            if enable_hostdev_iface:
                hostdev_pci_id = get_hostdev_addr_from_xml()
            vf_path = utils_misc.get_pci_path(hostdev_pci_id)
            check_vfio_pci(vf_path)
            if cmd_during_mig:
                s, o = utils_misc.cmd_status_output(cmd_during_mig, shell=True,
                                                    session=vm_session)
                if s:
                    test.fail("Failed to run %s in vm." % cmd_during_mig)

        if extra.count("--postcopy"):
            action_during_mig = virsh.migrate_postcopy
        if cancel_migration:
            action_during_mig = migration_test.do_cancel

        remove_dict = {"do_search": '{"%s": "ssh:/"}' % dest_uri}
        src_libvirt_file = libvirt_config.remove_key_for_modular_daemon(
            remove_dict)

        # Execute migration process
        vms = [vm]

        migration_test.do_migration(vms, None, dest_uri, 'orderly',
                                    options, thread_timeout=900,
                                    ignore_status=True, virsh_opt=virsh_options,
                                    func=action_during_mig, extra_opts=extra,
                                    **extra_args)
        mig_result = migration_test.ret

        if int(mig_result.exit_status) == 0:
            server_session = remote.wait_for_login('ssh', server_ip, '22',
                                                   server_user, server_pwd,
                                                   r"[\#\$]\s*$")
            check_vm_network_accessed(server_session, vm_ipv4)
            server_session.close()
            if net_failover_test:
                # Check network connection
                if enable_hostdev_iface:
                    check_vm_network_connection(net_hostdev_name)
                check_vm_network_connection(net_bridge_name)
                # VF driver should not be vfio-pci
                check_vfio_pci(vf_path, True)
                vm.connect_uri = dest_uri
                vm_after_mig = vm.wait_for_serial_login(timeout=240)
                cmd = "ip link"
                cmd_result = vm_after_mig.cmd_output(cmd)
                vm.connect_uri = bk_uri
                p_iface = re.findall(r"\d+:\s+(\w+):\s+.*", cmd_result)
                p_iface = [x for x in p_iface if x != 'lo']
                check_vm_iface_num(p_iface)

                # Check the output of ping command
                cmd = 'cat %s' % vm_tmp_file
                cmd_result = vm_after_mig.cmd_output(cmd)

                if re.findall('Destination Host Unreachable', cmd_result, re.M):
                    test.fail("The network does not work well during "
                              "the migration peirod. ping output: %s"
                              % cmd_result)

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
                logging.debug("migration back done")
                check_vm_network_accessed(ping_dest=vm_ipv4)
                if net_failover_test:
                    vm.cleanup_serial_console()
                    vm.create_serial_console()
                    vm_session = vm.wait_for_serial_login(timeout=240)
                    iface_list = get_vm_ifaces(vm_session)
                    check_vm_iface_num(iface_list)

        else:
            check_vm_network_accessed(ping_dest=vm_ipv4)
            if net_failover_test:
                iface_list = get_vm_ifaces(vm_session)
                check_vm_iface_num(iface_list)

    finally:
        logging.debug("Recover test environment")
        vm.connect_uri = bk_uri
        # Clean VM on destination
        migration_test.cleanup_vm(vm, dest_uri)

        logging.info("Recover VM XML configration")
        orig_config_xml.sync()
        logging.debug("The current VM XML:\n%s", orig_config_xml.xmltreefile)

        if src_libvirt_file:
            src_libvirt_file.restore()
        if remote_libvirt_file:
            del remote_libvirt_file

        server_session = remote.wait_for_login('ssh', server_ip, '22',
                                               server_user, server_pwd,
                                               r"[\#\$]\s*$")
        if 'src_pf' in locals():
            cmd = 'echo %s  >/proc/sys/net/ipv4/conf/all/rp_filter' % default_src_rp_filter
            process.run(cmd, shell=True, verbose=True)
            utils_sriov.add_or_del_connection(params, is_del=True)
            create_or_del_networks(src_pf, params, is_del=True)

        if 'dest_pf' in locals():
            cmd = 'echo %s  >/proc/sys/net/ipv4/conf/all/rp_filter' % default_dest_rp_filter
            utils_misc.cmd_status_output(cmd, shell=True, session=server_session)
            utils_sriov.add_or_del_connection(destparams_dict, session=server_session,
                                              is_del=True)
            remote_virsh_session = virsh.VirshPersistent(**remote_virsh_dargs)
            create_or_del_networks(dest_pf, params,
                                   remote_virsh_session,
                                   is_del=True)
            remote_virsh_session.close_session()

        if 'dest_pf_pci_path' in locals() and default_dest_vf != vf_no:
            utils_sriov.set_vf(dest_pf_pci_path, default_dest_vf, server_session)
        if 'src_pf_pci_path' in locals() and default_src_vf != vf_no:
            utils_sriov.set_vf(src_pf_pci_path, default_src_vf)

        # Clean up of pre migration setup for local machine
        if migr_vm_back:
            if 'ssh_connection' in locals():
                ssh_connection.auto_recover = True
            migration_test.migrate_pre_setup(src_uri, params,
                                             cleanup=True)

        server_session.close()
        if remote_virsh_session:
            remote_virsh_session.close_session()

        logging.info("Remove local NFS image")
        source_file = params.get("source_file")
        if source_file:
            libvirt.delete_local_disk("file", path=source_file)
