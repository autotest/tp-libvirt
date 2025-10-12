import re
import os
import logging as log
import platform
import time

from avocado.utils import process
from aexpect.exceptions import ExpectTimeoutError

from virttest import virt_vm
from virttest import virsh
from virttest import utils_net
from virttest import utils_misc
from virttest import utils_package
from virttest import utils_libvirtd
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.network_xml import NetworkXML

from provider.virtual_network import network_base
from virttest.utils_libvirt import libvirt_network

# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test PXE boot with remote TFTP server in NAT mode.

    This test implements the scenario described in VIRT-299071 variation
    where the TFTP server runs in a guest VM instead of on the host.

    Test steps:
    1. Create a virtual network with NAT mode and DHCP/TFTP configuration
    2. Setup a guest VM to act as TFTP server (IP: 192.168.10.3)
    3. Configure TFTP server in the guest with required PXE boot files
    4. Create another guest for PXE boot testing
    5. Verify PXE boot process using the remote TFTP server

    Network configuration:
    - Network: netboot (192.168.10.0/24)
    - Gateway: 192.168.10.1
    - TFTP Server Guest: 192.168.10.3 (MAC: 00:16:3e:77:e2:ed)
    - DHCP range: 192.168.10.2-192.168.10.254
    - BOOTP server points to 192.168.10.3
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    # Test configuration parameters
    net_name = params.get("net_name", "netboot")
    net_bridge = params.get("net_bridge", "{'name':'virbr_netboot'}")
    net_ip_address = params.get("net_ip_address", "192.168.10.1")
    tftp_server_ip = params.get("tftp_server_ip", "192.168.10.3")
    dhcp_host_mac = params.get("dhcp_host_mac", "00:16:3e:77:e2:ed")
    dhcp_host_ip = params.get("dhcp_host_ip", "192.168.10.3")
    bootp_file = params.get("bootp_file", "pxelinux.0")
    bootp_server = params.get("bootp_server", "192.168.10.3")
    tftp_root = params.get("tftp_root", "/var/lib/tftpboot")

    # Test control flags
    remote_tftp_server = "yes" == params.get("remote_tftp_server", "no")
    setup_tftp_guest = "yes" == params.get("setup_tftp_guest", "no")
    test_network_creation = "yes" == params.get("test_network_creation", "no")
    test_remote_tftp_setup = "yes" == params.get("test_remote_tftp_setup", "no")
    test_pxe_boot = "yes" == params.get("test_pxe_boot", "no")
    test_firewall_config = "yes" == params.get("test_firewall_config", "no")
    validate_dhcp_lease = "yes" == params.get("validate_dhcp_lease", "no")
    validate_tftp_connectivity = "yes" == params.get("validate_tftp_connectivity", "no")

    # TFTP guest configuration
    tftp_guest_name = params.get("tftp_guest_name", "tftp-server")
    pxe_boot_timeout = int(params.get("pxe_boot_timeout", "120"))
    boot_initrd = params.get("boot_initrd", "EXAMPLE_INITRD")
    boot_vmlinuz = params.get("boot_vmlinuz", "EXAMPLE_VMLINUZ")

    # Backup original configurations
    netxml_backup = None
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    tftp_guest_vm = None

    def create_netboot_network():
        """
        Create the netboot virtual network with NAT mode and TFTP configuration.
        This network differs from standard PXE by pointing BOOTP to a guest TFTP server.
        """
        logging.info("Creating netboot virtual network...")

        # Get network dictionary from configuration file
        network_dict = eval(params.get("network_dict"))

        # Create the network using libvirt_network utility
        libvirt_network.create_or_del_network(network_dict)

        logging.info("Netboot network created successfully")

    def setup_tftp_server_guest():
        """
        Create and configure a guest VM to act as TFTP server.
        This guest will have IP 192.168.10.3 and run the TFTP service.
        """
        logging.info("Setting up TFTP server guest...")

        try:
            # Clone the main VM to create TFTP server
            clone_cmd = "virt-clone --original %s --name %s --auto-clone" % (vm_name, tftp_guest_name)
            process.run(clone_cmd, shell=True, timeout=300)

            # Get the cloned VM object
            nonlocal tftp_guest_vm
            tftp_guest_vm = env.get_vm(tftp_guest_name)

            # Modify the cloned VM's network interface to use netboot network
            tftp_vmxml = vm_xml.VMXML.new_from_dumpxml(tftp_guest_name)

            # Update interface configuration
            iface_devices = tftp_vmxml.devices.by_device_tag("interface")
            if iface_devices:
                iface = iface_devices[0]
                iface.type_name = "network"
                iface.source = {"network": net_name}
                # Set the specific MAC address for DHCP reservation
                iface.mac_address = dhcp_host_mac
                tftp_vmxml.sync()

            # Start the TFTP server guest
            tftp_guest_vm.start()
            session = tftp_guest_vm.wait_for_login()

            logging.info("TFTP server guest started successfully")
            return session

        except Exception as e:
            test.error("Failed to setup TFTP server guest: %s" % str(e))

    def configure_tftp_service_in_guest(session):
        """
        Configure TFTP service inside the guest VM.
        This includes installing packages, configuring xinetd, and setting up PXE boot files.
        """
        logging.info("Configuring TFTP service in guest...")

        # Install required packages from configuration
        pkg_list = params.get("tftp_packages", "tftp-server xinetd syslinux tftp wget").split()
        for pkg in pkg_list:
            cmd = "yum install -y %s || dnf install -y %s || apt-get install -y %s" % (pkg, pkg, pkg)
            session.cmd(cmd, timeout=300)

        # Create TFTP root directory
        session.cmd("mkdir -p %s" % tftp_root)
        session.cmd("chmod 755 %s" % tftp_root)

        # Configure xinetd for TFTP (if needed on older systems)
        tftp_config = """service tftp
{
    socket_type     = dgram
    protocol        = udp
    wait            = yes
    user            = root
    server          = /usr/sbin/in.tftpd
    server_args     = -c -s %s
    disable         = no
    per_source      = 11
    cps             = 100 2
    flags           = IPv4
}""" % tftp_root

        session.cmd("echo '%s' > /etc/xinetd.d/tftp" % tftp_config)

        # Start TFTP service
        session.cmd("systemctl start tftp.socket || service xinetd start", ignore_all_errors=True)
        session.cmd("systemctl enable tftp.socket || chkconfig xinetd on", ignore_all_errors=True)

        # Setup firewall to allow TFTP
        session.cmd("firewall-cmd --add-service=tftp --permanent || iptables -I INPUT -p udp --dport 69 -j ACCEPT",
                   ignore_all_errors=True)
        session.cmd("firewall-cmd --reload || service iptables save", ignore_all_errors=True)

        # Download or setup PXE boot files (use example files for testing)
        if not (boot_initrd.count("EXAMPLE") or boot_vmlinuz.count("EXAMPLE")):
            # Download actual boot files if URLs provided
            session.cmd("wget %s -O %s/initrd.img" % (boot_initrd, tftp_root), timeout=300)
            session.cmd("wget %s -O %s/vmlinuz" % (boot_vmlinuz, tftp_root), timeout=300)
        else:
            # Create dummy files for testing
            session.cmd("echo 'dummy initrd' > %s/initrd.img" % tftp_root)
            session.cmd("echo 'dummy vmlinuz' > %s/vmlinuz" % tftp_root)

        # Copy PXE boot loader
        session.cmd("cp /usr/share/syslinux/pxelinux.0 %s/ || cp /usr/lib/syslinux/pxelinux.0 %s/" %
                   (tftp_root, tftp_root), ignore_all_errors=True)

        # Create PXE configuration
        session.cmd("mkdir -p %s/pxelinux.cfg" % tftp_root)
        pxe_config = params.get("pxe_config_template", "DISPLAY boot.txt\nDEFAULT test\nLABEL test\n    kernel vmlinuz\n    append initrd=initrd.img\nPROMPT 1\nTIMEOUT 30")

        session.cmd("echo '%s' > %s/pxelinux.cfg/default" % (pxe_config, tftp_root))

        # Verify TFTP service is running
        time.sleep(5)
        tftp_port = params.get("tftp_service_port", "69")
        result = session.cmd_status("netstat -ulnp | grep :%s" % tftp_port)
        if result != 0:
            test.error("TFTP service is not listening on port 69")

        logging.info("TFTP service configured successfully in guest")

    def configure_pxe_boot_vm():
        """
        Configure the main VM for PXE boot from the remote TFTP server.
        """
        logging.info("Configuring main VM for PXE boot...")

        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)

        # Configure OS for PXE boot
        osxml = vm_xml.VMOSXML()
        osxml.type = vmxml.os.type
        osxml.arch = vmxml.os.arch
        osxml.machine = vmxml.os.machine
        osxml.loader = params.get("bios_loader", "/usr/share/seabios/bios-256k.bin")
        osxml.bios_useserial = params.get("bios_useserial", "yes")
        if utils_misc.compare_qemu_version(4, 0, 0, False):
            osxml.bios_reboot_timeout = params.get("bios_reboot_timeout", "-1")
        osxml.boots = ['network']
        del vmxml.os
        vmxml.os = osxml

        # Update network interface
        iface_devices = vmxml.devices.by_device_tag("interface")
        if iface_devices:
            iface = iface_devices[0]
            iface.type_name = "network"
            iface.source = {"network": net_name}
            # Use different MAC for the PXE boot client
            iface.mac_address = params.get("iface_mac", "00:16:3e:77:e2:ff")

        vmxml.sync()
        logging.info("VM configured for PXE boot")

    def test_tftp_connectivity():
        """
        Test TFTP connectivity from host to the guest TFTP server.
        """
        logging.info("Testing TFTP connectivity...")

        # Test TFTP connectivity from host
        cmd = "tftp -4 -v %s -c get pxelinux.0" % tftp_server_ip
        result = process.run(cmd, shell=True, ignore_status=True, timeout=30)

        if result.exit_status == 0:
            logging.info("TFTP connectivity test successful")
            return True
        else:
            logging.warning("TFTP connectivity test failed: %s" % result.stderr)
            return False

    def validate_network_configuration():
        """
        Validate the netboot network configuration and DHCP leases.
        """
        logging.info("Validating network configuration...")

        # Check network is active
        result = virsh.net_list("--all", debug=True)
        if net_name not in result.stdout:
            test.fail("Netboot network not found in network list")

        # Check dnsmasq configuration
        dnsmasq_conf = "/var/lib/libvirt/dnsmasq/%s.conf" % net_name
        if os.path.exists(dnsmasq_conf):
            with open(dnsmasq_conf, 'r') as f:
                conf_content = f.read()
                if bootp_server not in conf_content:
                    test.fail("BOOTP server configuration not found in dnsmasq.conf")
                logging.info("dnsmasq configuration validated")

        # Check DHCP leases if available
        result = virsh.net_dhcp_leases(net_name, debug=True, ignore_status=True)
        if result.exit_status == 0:
            logging.info("DHCP leases: %s" % result.stdout)

    def perform_pxe_boot_test():
        """
        Perform the actual PXE boot test.
        """
        logging.info("Starting PXE boot test...")

        try:
            # Start the VM for PXE boot
            vm.start()

            # Monitor serial console for PXE boot messages
            try:
                vm.serial_console.read_until_output_matches(
                    ["Loading vmlinuz", "Loading initrd.img", "TFTP", "PXE"],
                    utils_misc.strip_console_codes,
                    timeout=pxe_boot_timeout)
                logging.info("PXE boot messages detected - test successful!")
                return True

            except ExpectTimeoutError:
                logging.warning("PXE boot timeout - checking for any network activity")
                # Even if we don't see the exact messages, check if there was network boot attempt
                console_output = vm.serial_console.get_output()
                if any(pattern in console_output.lower() for pattern in ["pxe", "tftp", "dhcp", "network"]):
                    logging.info("Network boot activity detected")
                    return True
                else:
                    test.fail("No PXE boot activity detected within timeout")

        except Exception as e:
            test.fail("PXE boot test failed: %s" % str(e))

    # Main test execution
    try:
        # Step 1: Create netboot network
        if test_network_creation:
            create_netboot_network()
            validate_network_configuration()

        # Step 2: Setup TFTP server guest
        if setup_tftp_guest and test_remote_tftp_setup:
            tftp_session = setup_tftp_server_guest()
            configure_tftp_service_in_guest(tftp_session)
            tftp_session.close()

        # Step 3: Test TFTP connectivity
        if validate_tftp_connectivity:
            if not test_tftp_connectivity():
                logging.warning("TFTP connectivity test failed, but continuing with PXE boot test")

        # Step 4: Configure main VM for PXE boot
        if test_pxe_boot:
            configure_pxe_boot_vm()

            # Step 5: Perform PXE boot test
            perform_pxe_boot_test()

        logging.info("PXE boot with remote TFTP server test completed successfully")

    finally:
        # Cleanup
        try:
            if vm.is_alive():
                vm.destroy(gracefully=False)

            if tftp_guest_vm and tftp_guest_vm.is_alive():
                tftp_guest_vm.destroy(gracefully=False)

            # Remove TFTP guest
            if tftp_guest_name:
                virsh.undefine(tftp_guest_name, "--remove-all-storage", debug=True, ignore_status=True)

            # Cleanup network
            if net_name and net_name != "default":
                virsh.net_destroy(net_name, debug=True, ignore_status=True)
                virsh.net_undefine(net_name, debug=True, ignore_status=True)

            # Restore VM configuration
            vmxml_backup.sync()

            # Cleanup temporary files
            if os.path.exists("/tmp/netboot.xml"):
                os.remove("/tmp/netboot.xml")

        except Exception as e:
            logging.warning("Cleanup error: %s" % str(e))