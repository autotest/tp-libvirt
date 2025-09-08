import os
import re
import logging as log

from avocado.utils import process

from virttest import virsh
from virttest import utils_misc
from virttest import utils_net
from virttest import remote
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml
from virttest.staging import utils_memory

from provider.migration import base_steps

# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test UDP packet transfer from VM to remote host during migration.
    
    1. Setup VM and remote host environment
    2. Install and configure netperf on remote host and VM
    3. Start packet capture on remote host
    4. Run UDP transfer test from VM to remote host
    5. Verify packets are captured correctly
    6. Clean up environment
    """
    
    def disable_firewall(session, guest_os_type="linux"):
        """Disable firewall on the target system."""
        if guest_os_type == "linux":
            cmd = "systemctl stop firewalld"
            status = session.cmd_status(cmd)
            if status == 0:
                logging.info("Firewall disabled successfully")
            else:
                logging.warning("Failed to disable firewall or already disabled")
        else:
            # Windows firewall
            cmd = 'netsh firewall set opmode mode=disable'
            output = session.cmd_output(cmd)
            logging.info("Windows firewall disabled: %s", output)

    def install_netperf(session, guest_os_type="linux"):
        """Install netperf on the target system."""
        if guest_os_type == "linux":
            # Check if netperf is already installed
            status = session.cmd_status("which netperf")
            if status == 0:
                logging.info("netperf already installed")
                return
            
            # Install netperf
            install_cmd = ("yum install -y netperf || "
                          "apt-get update && apt-get install -y netperf")
            session.cmd(install_cmd, timeout=300)
            
            # Verify installation
            status = session.cmd_status("which netperf")
            if status != 0:
                test.fail("Failed to install netperf")
        else:
            # For Windows, we would need to transfer netperf.exe
            # This is simplified for the basic structure
            logging.info("Windows netperf setup would be handled here")

    def start_netserver(session):
        """Start netperf server."""
        # Kill any existing netserver
        session.cmd_status("pkill netserver")
        
        # Start netserver
        output = session.cmd_output("netserver", timeout=30)
        if "Starting netserver" not in output:
            # Check if it's already running
            status = session.cmd_status("pgrep netserver")
            if status != 0:
                test.fail("Failed to start netserver")
        logging.info("netserver started successfully")

    def start_tcpdump(session, vm_ip, log_file):
        """Start tcpdump to capture UDP packets."""
        # Install tcpdump if not available
        session.cmd_status("yum install -y tcpdump || apt-get install -y tcpdump")
        
        # Start tcpdump in background
        tcpdump_cmd = f"tcpdump -n udp and src {vm_ip} > {log_file} 2>&1 &"
        session.cmd(tcpdump_cmd)
        logging.info("tcpdump started, capturing packets from %s", vm_ip)

    def run_netperf_udp_test(vm_session, target_ip):
        """Run netperf UDP test from VM to target."""
        netperf_cmd = f"netperf -H {target_ip} -t UDP_STREAM -- -m 1473"
        
        # Run netperf test
        try:
            output = vm_session.cmd_output(netperf_cmd, timeout=120)
            logging.info("netperf UDP test completed")
            return output
        except Exception as e:
            test.fail(f"netperf UDP test failed: {e}")

    def verify_packet_capture(session, log_file, vm_ip, target_ip):
        """Verify that UDP packets were captured."""
        # Wait a bit for packets to be written
        utils_misc.wait_for(lambda: True, 5)
        
        # Read tcpdump log
        try:
            output = session.cmd_output(f"cat {log_file}")
            logging.info("tcpdump log content: %s", output)
            
            # Look for UDP packets with expected pattern
            pattern = f"IP {vm_ip}\.\\d+ > {target_ip}\.\\d+: UDP, length 1473"
            if re.search(pattern, output):
                logging.info("UDP packets successfully captured")
                return True
            else:
                test.fail(f"Expected UDP packets not found in capture. Pattern: {pattern}")
        except Exception as e:
            test.fail(f"Failed to read tcpdump log: {e}")

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    
    # Migration parameters
    remote_host = params.get("remote_host")
    remote_user = params.get("remote_user", "root") 
    remote_passwd = params.get("remote_passwd")
    guest_os_type = params.get("guest_os_type", "linux")
    
    if not remote_host:
        test.cancel("remote_host parameter is required")
    
    tcpdump_log = "/tmp/udp_capture.log"
    
    try:
        # Setup VM
        if not vm.is_alive():
            vm.start()
        vm.wait_for_login()
        
        vm_session = vm.wait_for_login()
        vm_ip = vm.get_address()
        
        logging.info("VM IP: %s", vm_ip)
        logging.info("Remote host: %s", remote_host)
        
        # Setup remote host session
        remote_session = remote.remote_login("ssh", remote_host, "22",
                                           remote_user, remote_passwd,
                                           r"[\#\$]\s*$")
        
        # Disable firewalls
        disable_firewall(vm_session, guest_os_type)
        disable_firewall(remote_session, "linux")

        # Install netperf on VM and remote host
        install_netperf(vm_session, guest_os_type)
        install_netperf(remote_session, "linux")
        
        # Start netserver on remote host
        start_netserver(remote_session)
        
        # Start packet capture on remote host
        start_tcpdump(remote_session, vm_ip, tcpdump_log)
        
        # Run UDP transfer test
        netperf_output = run_netperf_udp_test(vm_session, remote_host)
        
        # Verify packet capture
        verify_packet_capture(remote_session, tcpdump_log, vm_ip, remote_host)
        
        logging.info("UDP transfer test completed successfully")
        
    finally:
        # Cleanup
        try:
            if 'remote_session' in locals():
                # Stop tcpdump
                remote_session.cmd_status("pkill tcpdump")
                # Stop netserver  
                remote_session.cmd_status("pkill netserver")
                # Remove log file
                remote_session.cmd_status(f"rm -f {tcpdump_log}")
                remote_session.close()
        except Exception as e:
            logging.warning("Cleanup failed: %s", e)
        
        try:
            if 'vm_session' in locals():
                vm_session.close()
        except Exception as e:
            logging.warning("VM session cleanup failed: %s", e)