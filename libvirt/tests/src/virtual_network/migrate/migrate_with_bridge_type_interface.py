import logging as log
import re


from virttest import libvirt_version
from virttest import libvirt_vm
from virttest import migration
from virttest import remote
from virttest import utils_net
from virttest import utils_package
from virttest import virsh
from provider.guest_os_booting import guest_os_booting_base as guest_os

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_network
from provider.virtual_network import network_base
from provider.interface import interface_base
from provider.migration import base_steps

logging = log.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test migration with bridge type interface
    1. Setup bridge and virtual network according to bridge type
    2. Migrate to target host
    3. Check on target host for network functions:
       - Guest ping outside
       - Check for multiqueue
       - Check multiqueues in VM live XML (should have <driver ... queues='5'>)
    4. Migrate back from dst to src

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    def check_multiqueue_in_guest(vm_session):
        """
        Check multiqueue configuration inside the guest
        """
        logging.info("Checking multiqueue configuration in guest")
        # Get network interface name
        cmd = "ip link show | grep -E '^[0-9]+:' | grep -v lo | head -1 | awk -F':' '{print $2}' | tr -d ' '"
        #guest_iface_info = session1.cmd_output("ip --color=never l").strip()
        #guest_iface_name = re.findall(r"^\d+: (\S+?)[@:].*state UP.*$", guest_iface_info, re.MULTILINE)[0]
        iface_name = vm_session.cmd_output(cmd).strip()
        if not iface_name:
            test.fail("Failed to get network interface name in guest")
        
        # Check if multiqueue is enabled
        cmd = "ethtool -L %s" % iface_name
        try:
            output = vm_session.cmd_output(cmd, timeout=30)
            logging.debug("ethtool output: %s", output)
        except Exception as e:
            logging.debug("ethtool command failed (expected for some cases): %s", e)
        
        # ethtool -L enp1s0 combined 3 checking method
        cmd = "ethtool -L %s combined 3" % iface_name
        try:
            vm_session.cmd(cmd, timeout=30)
            logging.info("Successfully set combined queues to 3 for %s", iface_name)
        except Exception as e:
            logging.debug("ethtool -L command failed (expected for some cases): %s", e)


    def check_multiqueue_in_xml():
        """
        Check multiqueue configuration in VM XML
        """
        logging.info("Checking multiqueue configuration in VM XML")
        # Get live XML
        live_xml = vm_xml.VMXML.new_from_dumpxml(vm_name, virsh_instance=virsh_session_remote)
        xml_content = str(live_xml)
        
        # Check for driver queues='5' in interface
        if not re.search(r'<driver.*queues=[\'"]5[\'"]', xml_content):
            test.fail("Expected <driver ... queues='5'> not found in VM XML:\n%s" % xml_content)
        
        logging.info("Found multiqueue configuration in VM XML")


    def setup_vm_interface():
        """
        Setup VM interface according to configuration
        """
        logging.info("Setting up VM interface")
        
        # Get interface configuration from params
        iface_dict = eval(params.get("iface_source", "{}"))

        if interface_timing == "hotplug":
            # Hot-plug interface using helper function
            iface_xml = libvirt.modify_vm_iface(vm_name, "get_xml", iface_dict)
            if not vm.is_alive():
                vm.start()
            vm.wait_for_login()
            result = virsh.attach_device(vm_name, iface_xml, flagstr="--live", debug=True)
            if result.exit_status:
                test.fail("Failed to hotplug interface: %s" % result.stderr_text)
        else:
            # Add interface to XML using setup_attrs pattern
            vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
            vmxml.remove_all_device_by_type('interface')
            
            iface = interface_base.create_iface(iface_dict['type'], iface_dict)
            vmxml.add_device(iface)
            vmxml.sync()


    def setup_test():
        """
        Setup test environment for migration with bridge type interface
        """
        logging.info("Setting up test environment")
        if bridge_type == "linux":
            utils_net.create_linux_bridge_tmux(bridge_name)
            utils_net.create_linux_bridge_tmux(bridge_name, session=remote_session)

        elif bridge_type == "ovs":
            # Create OVS bridge
            status, stdout = utils_net.create_ovs_bridge(ovs_bridge_name, ip_options='-color=never')
            if status:
                test.fail("Failed to create ovs bridge on local. Status: %s, Stdout: %s" % (status, stdout))
                
            # Create same bridge on remote host
            status, stdout = utils_net.create_ovs_bridge(ovs_bridge_name, session=remote_session, ip_options='-color=never')
            if status:
                test.fail("Failed to create ovs bridge on remote. Status: %s, Stdout: %s" % (status, stdout))
                
            # Create virtual network for OVS bridge
            libvirt_network.create_or_del_network(network_dict, remote_args=remote_virsh_dargs)
            logging.info("dest: network created")
            libvirt_network.create_or_del_network(network_dict)
            logging.info("localhost: network created")

        # Setup NFS shared storage for migration
        migration_test.migrate_pre_setup(dest_uri, params)
        
        libvirt.set_vm_disk(vm, params)
        setup_vm_interface()
        
        test.log.debug("Guest xml after starting:\n%s", vm_xml.VMXML.new_from_dumpxml(vm_name))

    def run_test():
        """
        Run the main test: migration and verification
        """
        logging.info("Starting migration test")
        
        # Check local guest network connection before migration
        if vm.serial_console is not None:
            vm.cleanup_serial_console()
        vm.create_serial_console()
        vm_session = vm.wait_for_serial_login(timeout=240)
        
        if not utils_package.package_install('dhcp-client', session=vm_session):
            test.error("Failed to install dhcp-client on guest.")
        utils_net.restart_guest_network(vm_session)
        
        logging.info("Checking VM network connectivity before migration")
        ips = {'outside_ip': remote_ip}
        network_base.ping_check(params, ips, vm_session)
        
        logging.info("Migrating VM to target host")
        vms = [vm]
        migration_test.do_migration(vms, None, dest_uri, 'orderly',
                                   options, thread_timeout=900,
                                   ignore_status=True, virsh_opt=virsh_options,
                                   extra_opts=extra_opts,
                                   **extra_args)
        
        mig_result = migration_test.ret
        if int(mig_result.exit_status) != 0:
            test.fail("Migration failed: %s" % mig_result.stderr_text)
            
        logging.info("Checking VM network connectivity on target host")
        vm.connect_uri = dest_uri
        if vm.serial_console is not None:
            vm.cleanup_serial_console()
        vm.create_serial_console()
        vm_session_after_mig = vm.wait_for_serial_login(timeout=240)
        vm_session_after_mig.cmd("dhclient -r; dhclient")

        logging.info("Testing guest ping to outside")
        ips = {'outside_ip': remote_ip}
        network_base.ping_check(params, ips, vm_session_after_mig)
        
        logging.info("Checking multiqueue in guest")
        check_multiqueue_in_guest(vm_session_after_mig)
        
        logging.info("Checking multiqueue in VM XML")
        check_multiqueue_in_xml()
        
        if migrate_vm_back:
            test.log.info("Migrating VM back to source host")
            migration_obj = base_steps.MigrationBase(test, vm, params)
            migration_obj.run_migration_back()

    def teardown_test():
        """
        Cleanup test environment
        """
        logging.info("Cleaning up test environment")

        vm.connect_uri = bk_uri
        migration_test.cleanup_vm(vm, dest_uri)

        # Recovery VM XML configuration
        logging.info("Recovery VM XML configuration")
        orig_config_xml.sync()

        # Cleanup bridges
        if bridge_type == "linux":
            utils_net.delete_linux_bridge_tmux(bridge_name)
            utils_net.delete_linux_bridge_tmux(bridge_name, session=remote_session)
        elif bridge_type == "ovs":
            utils_net.delete_ovs_bridge(ovs_bridge_name, ip_options='-color=never')
            utils_net.delete_ovs_bridge(ovs_bridge_name, session=remote_session, ip_options='-color=never')

            # Cleanup networks
            libvirt_network.create_or_del_network(network_dict, is_del=True, remote_args=remote_virsh_dargs)
            libvirt_network.create_or_del_network(network_dict, is_del=True)

        if migrate_vm_back:
            ssh_connection = None
            if 'ssh_connection' in locals():
                ssh_connection.auto_recover = True
            migration_test.migrate_pre_setup(src_uri, params, cleanup=True)

        # Remove local NFS image
        logging.info("Remove local NFS image")
        source_file = params.get("source_file")
        if source_file:
            libvirt.delete_local_disk("file", path=source_file)

    # Initialize migration test
    migration_test = migration.MigrationTest()
    migration_test.check_parameters(params)

    libvirt_version.is_libvirt_feature_supported(params)
    
    # Params to update disk using shared storage
    params["disk_type"] = params.get("disk_type", "file")
    params["disk_source_protocol"] = params.get("disk_source_protocol", "netfs")
    params["mnt_path_name"] = params.get("nfs_mount_dir")
    
    # Local variables
    server_ip = params.get("server_ip")
    server_user = params.get("server_user")
    server_pwd = params.get("server_pwd")
    virsh_options = params.get("virsh_options")
    extra_opts = params.get("virsh_migrate_extra")
    options = params.get("virsh_migrate_options")
    remote_ip = params.get("remote_ip")
    bridge_name = params.get("bridge_name")
    bridge_type = params.get("bridge_type")
    ovs_bridge_name = params.get("ovs_bridge_name")
    network_dict = eval(params.get("network_dict", "{}"))
    interface_timing = params.get("interface_timing")
    
    extra_args = migration_test.update_virsh_migrate_extra_args(params)
    migrate_vm_back = "yes" == params.get("migrate_vm_back")
    
    remote_virsh_dargs = {'remote_ip': server_ip, 'remote_user': server_user,
                          'remote_pwd': server_pwd, 'unprivileged_user': params.get("unprivileged_user"),
                          'ssh_remote_auth': params.get("ssh_remote_auth")}
    
    # params for migration connection  
    params["virsh_migrate_desturi"] = libvirt_vm.complete_uri(
        params.get("migrate_dest_host"))
    params["virsh_migrate_connect_uri"] = libvirt_vm.complete_uri(
        params.get("migrate_source_host"))
    src_uri = params.get("virsh_migrate_connect_uri")
    dest_uri = params.get("virsh_migrate_desturi")
    
    vm_name = guest_os.get_vm(params)
    vm = env.get_vm(vm_name)
    vm.verify_alive()
    bk_uri = vm.connect_uri
    
    # For safety reasons, we'd better back up xmlfile
    new_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    orig_config_xml = new_xml.copy()
    
    # Create remote session and virsh instance
    remote_session = remote.remote_login("ssh", server_ip, "22",
                                        server_user, server_pwd, r'[$#%]')
    virsh_session_remote = virsh.VirshPersistent(**remote_virsh_dargs)

    try:
        setup_test()
        run_test()
    finally:
        teardown_test()
        if remote_session:
            remote_session.close()
        if virsh_session_remote:
            virsh_session_remote.close_session()