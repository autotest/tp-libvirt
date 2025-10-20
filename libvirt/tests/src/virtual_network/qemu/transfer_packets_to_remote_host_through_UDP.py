import os
import re
import logging as log

from avocado.utils import process

from virttest import utils_misc
from virttest import utils_net
from virttest import utils_package
from virttest import remote
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from provider.virtual_network import network_base

from provider.virtual_network import utils_win


logging = log.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test UDP packet transfer from VM to remote host.
    
    1. Setup VM and remote host environment
    2. Install and configure netperf on remote host and VM
    3. Start packet capture on remote host
    4. Run UDP transfer test from VM to remote host
    5. Verify packets are captured correctly
    6. Clean up environment
    """

    def disable_firewall(session, params, guest_os_type="linux"):
        """
        Disable firewall on the target system.
        :param session: Session object for executing commands
        :param params: Params object
        :param guest_os_type: OS type ("linux" or "windows")
        """
        firewall_cmd = params.get("firewall_cmd")

        if guest_os_type == "linux":
            status = session.cmd_status(firewall_cmd)
            if status != 0:
                test.log.debug("Failed to disable firewall or already disabled")
        else:
            output = session.cmd_output(firewall_cmd)
            test.log.debug("Windows firewall disabled: %s", output)


    def transfer_netperf(guest_session, guest_os_type, src_guest_ip, src_host_ip, password, netperf_install_path='C:\\Program Files'):
        """
        Transfer and install netperf.

        :param guest_session: Guest session object
        :param guest_os_type: Guest OS type ("linux" or "windows")
        :param src_guest_ip: Source guest IP address
        :param src_host_ip: Source host IP address
        :param password: Password for authentication
        :param netperf_install_path: Installation path for Windows netperf
        """
        if guest_os_type == 'linux':
            test.log.debug('Transfer netperf tar to guest')
            list_cmd = 'ls -ld /home/%s' % netperf_version
            status, output = guest_session.cmd_status_output(list_cmd)
            if r'No such file or directory' not in output:
                guest_session.cmd('rm -rf /home/%s' % netperf_version)

            out = process.run('scp %s root@%s:/home' % (netperf_linux_path, src_guest_ip))
            test.log.debug("44444444444444444444:%s", out)

            test.log.debug('Install netperf in guest')
            guest_session.cmd("yum -y install automake autoconf libtool")
            install_cmd = ('tar jxf /home/%s.tar.bz2 -C /home/ && '
                          'cd /home/%s && export CFLAGS="-D_GNU_SOURCE" && ./autogen.sh && '
                          './configure && make && make install')

            guest_session.cmd(install_cmd % (netperf_version, netperf_version), timeout=600)

            status, output = guest_session.cmd_status_output(list_cmd)
            if r'No such file or directory' in output:
                test.fail('Fail to install netperf in guest')
        else:
            utils_win.pscp_file(guest_session, netperf_windows_path,
                                netperf_install_path, src_host_ip, 'root', password)
            check_installed_cmd = 'dir "%s"|findstr /I netperf' % netperf_install_path
            output = guest_session.cmd_output(check_installed_cmd)
            if 'netperf' in output:
                test.log.debug('Success to transfer netperf.exe from '
                                'src host to guest')
            else:
                test.fail('Fail to transfer netperf.exe from '
                               'src host to guest')


    def run_netserver(host_session):
        """
        Install and run netserver.

        :param host_session: Host session object for executing commands
        """
        test.log.debug('Install netserver on remote host firstly')
        remote_host = params.get("remote_host")
        remote_user = params.get("remote_user", "root")
        remote_passwd = params.get("remote_passwd")

        test.log.debug('Scp netperf to remote host.')
        utils_misc.make_dirs(os.path.dirname(netperf_linux_path), host_session)
        remote.copy_files_to(remote_host, 'scp', remote_user, remote_passwd,
                             '22', netperf_linux_path, netperf_linux_path)

        list_cmd = 'ls -ld /home/%s' % netperf_version
        if r'No such file or directory' not in \
                host_session.cmd_output(list_cmd):
            host_session.cmd_output('rm -rf /home/%s' % netperf_version)

        host_session.cmd_output("yum -y install automake autoconf libtool")
        install_cmd = 'tar jxf %s -C /home/ && ' \
                      'cd /home/%s && export CFLAGS="-D_GNU_SOURCE" && ./autogen.sh  && ' \
                      './configure && make && make install' % (netperf_linux_path, netperf_version)
        output = host_session.cmd_output(cmd=install_cmd)
        test.log.debug("3333333333333333333333:%s", output)
        if r'No such file or directory' in host_session.cmd_output(
                list_cmd):
            test.fail('Fail to install netperf on host')
        o = host_session.cmd_output('netstat -anp |grep 12865')
        if o:
            used_pid = o.split('LISTEN')[1].strip().split('/')[0]
            host_session.cmd_output('kill -9 %s' % used_pid)
            output = host_session.cmd_output('netserver')
        else:
            output = host_session.cmd_output('netserver')
        if re.search(r'libsctp', output):
            host_session.cmd_output('yum install -y libsctp*')
            output = host_session.cmd_output('netserver')
        if 'netserver' not in host_session.cmd_output(
                'pgrep -xl netserver') or ('Starting netserver' not in output):
            test.fail("Fail to start netserver")


    def run_netperf(guest_session, dst_host_ip, guest_os_type, packet_size, netperf_install_path='C:\\Program Files'):
        """
        Run netperf UDP test using tp-libvirt session methods.
        :param guest_session: Guest session object
        :param dst_host_ip: Destination host IP address
        :param guest_os_type: Guest OS type ("linux" or "windows")
        :param packet_size: UDP packet size for netperf test
        :param netperf_install_path: Installation path for Windows netperf
        :return: netperf log filename
        """
        netperf_log = 'netperf_log_%s' % utils_misc.generate_random_string(6)
        if guest_os_type == 'linux':
            guest_session.cmd('cd /home')
            guest_session.cmd('netperf -H %s -t UDP_STREAM -- -m %s > %s &' %
                              (dst_host_ip, packet_size, netperf_log))
        else:
            guest_session.cmd('cd %s' % netperf_install_path)
            guest_session.cmd('netperf.exe -H %s -t UDP_STREAM -- -m %s > %s &' %
                              (dst_host_ip, packet_size, netperf_log))
        return netperf_log


    def check_netperf_log(guest_session, netperf_log, guest_os_type, packet_size, netperf_install_path='C:\\Program Files'):
        """
        Check netperf log results using tp-libvirt session methods.
        :param guest_session: Guest session object
        :param netperf_log: Netperf log filename to check
        :param guest_os_type: Guest OS type ("linux" or "windows")
        :param packet_size: Expected UDP packet size in log
        :param netperf_install_path: Installation path for Windows netperf
        :return: netperf log filename
        """
        if guest_os_type == 'linux':
            if utils_misc.wait_for(
                    lambda: 'netperf' not in guest_session.cmd_output('pgrep -xl netperf'), 120, step=3.0):
                test.log.debug('Finish to execute netperf in guest')
            else:
                test.fail('Timeout to execute netperf in guest under 120s')
        else:
            cmd = 'tasklist /FI "imagename eq netperf.exe"'
            guest_session.cmd('cd %s' % netperf_install_path)
            if utils_misc.wait_for(lambda: not re.search(
                    r'netperf.exe', guest_session.cmd_output(cmd)), 120, step=3.0):
                test.log.debug('Finish to execute netperf in guest')
            else:
                test.fail('Timeout to execute netperf in guest under 120s')
        data_match = 'UDP STREAM TEST from'
        viewlog_cmd = 'cat /home/%s' % netperf_log
        if guest_os_type == 'windows':
            viewlog_cmd = 'type %s' % netperf_log
        output = guest_session.cmd_output(viewlog_cmd)
        if data_match and str(packet_size) in output:
            test.log.debug('The log of netperf checking is PASS')
        else:
            test.fail("The log of netperf isn't right:%s" % output)
        return netperf_log


    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    netperf_version = params.get("netperf_version")

    # Handle variable substitution in paths
    netperf_linux_rel = params.get('netperf_linux').replace('${netperf_version}', netperf_version)
    netperf_linux_path = os.path.join(os.path.dirname(__file__), netperf_linux_rel)
    netperf_windows_path = os.path.join(os.path.dirname(__file__), params.get('netperf_windows'))

    remote_host = params.get("remote_host")
    remote_ip = params.get("remote_ip")
    remote_user = params.get("remote_user", "root") 
    remote_passwd = params.get("remote_passwd")
    local_passwd = params.get("local_passwd")
    guest_os_type = params.get("guest_os_type", "linux")
    packet_size = params.get("udp_packet_size")
    tcpdump_log = "/tmp/udp_capture.log"

    vm_mac = utils_net.generate_mac_address_simple()
    tap_name = params.get("tap_name")
    bridge_name = params.get("bridge_name")
    tap_flag = params.get("tap_flag")
    iface_attrs = eval(params.get("iface_attrs", "[]") % tap_name)
    iface_attrs.update({'mac_address': vm_mac})
    guest_passwd = params.get("password")
    backup_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    # Initialize variables to avoid reference errors in cleanup
    vm_session = None
    remote_session = None
    tcpdump_log_file = None
    host_iface = None

    try:
        test.log.debug("TEST_STEP1: Boot up a guest on src host")
        host_iface = network_base.get_host_iface(test)
        utils_net.create_linux_bridge_tmux(bridge_name, host_iface)

        network_base.create_tap(tap_name, bridge_name, 'root', flag=tap_flag)
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        libvirt_vmxml.modify_vm_device(vmxml, 'interface', iface_attrs)
        if not vm.is_alive():
            vm.start()
        vm_session = vm.wait_for_serial_login()
        test.log.debug("Guest xml:\n%s", vm_xml.VMXML.new_from_dumpxml(vm_name))

        vm_ip = network_base.get_vm_ip(vm_session, vm_mac)
        test.log.debug("VM IP: %s", vm_ip)
        test.log.debug("Remote host: %s", remote_host)
        remote_session = remote.remote_login(
            "ssh", remote_host, "22", remote_user, remote_passwd, r"[\#\$]\s*$")

        # Install and run netserver on remote host
        test.log.debug("TEST_STEP2: Start netperf server on remote host")
        # Setup SSH keys for Linux guests
        if guest_os_type == 'linux':
            remote_session.cmd_output('sshpass -p %s ssh-copy-id -o '
                                     '"StrictHostKeyChecking no" -i '
                                     '/root/.ssh/id_rsa.pub root@%s' % (guest_passwd, vm_ip))
        run_netserver(remote_session)

        # Start packet capture on remote host
        test.log.debug("TEST_STEP3: Capture the packet from guest")
        if not utils_package.package_install('tcpdump', session=remote_session):
            test.fail("tcpdump package install failed")
        tcpdump_log_file = '/tmp/UDP_tcpdump.log'
        remote_session.sendline('tcpdump -n udp and src %s > %s 2>&1 &'
                                % (vm_ip, tcpdump_log_file))

        test.log.debug("TEST_STEP4: Run netperf client command in the guest")
        # Stop firewall for guest
        if guest_os_type == 'linux':
            process.run("yum -y install sshpass")
            process.run('sshpass -p %s ssh-copy-id -o "StrictHostKeyChecking no" -i '
                        '/root/.ssh/id_rsa.pub root@%s' % (local_passwd, vm_ip))
        disable_firewall(vm_session, params, guest_os_type)
        disable_firewall(remote_session, params, "linux")

        # Transfer and install netperf in guest.
        transfer_netperf(vm_session, guest_os_type, vm_ip,
                         remote_host, guest_passwd)

        try:
            netperf_log = run_netperf(vm_session, remote_host, guest_os_type, packet_size)
            check_netperf_log(vm_session, netperf_log, guest_os_type, packet_size)

        finally:
            if guest_os_type == 'linux':
                vm_session.sendline('rm -rf /home/netperf_log*')
            else:
                vm_session.sendline('del netperf_log*')

        test.log.debug("TEST_STEP5: Verify packet capture")
        # Use grep to search for expected packets in the large log file
        expected_pattern = r'IP %s\.\d+ > %s\.\d+: UDP, length %s' % (vm_ip, remote_ip, packet_size)
        test.log.debug("Expected regex pattern: %s", expected_pattern)
        test.log.debug("vm_ip: %s, remote_ip: %s, packet_size: %s", vm_ip, remote_ip, packet_size)

        # Use grep to find matching packets efficiently
        grep_cmd = 'grep -E "IP %s\\.[0-9]+ > %s\\.[0-9]+: UDP, length %s" %s | head -5' % (
            vm_ip, remote_ip, packet_size, tcpdump_log_file)
        test.log.debug("Grep command: %s", grep_cmd)

        try:
            grep_output = remote_session.cmd_output(grep_cmd)
            if grep_output.strip():
                test.log.debug("Found matching packets:")
                for line in grep_output.strip().split('\n')[:3]:  # Show first 3 matches
                    test.log.debug("  %s", line.strip())
                test.log.debug('Captured packets on the remote host match the expectation')
                packet_found = True
            else:
                test.log.debug("No matching packets found")
                packet_found = False
        except Exception as e:
            test.log.debug("Grep command failed: %s", str(e))
            packet_found = False

        if not packet_found:
            # Show some sample lines from the log for debugging
            test.log.debug("Showing first 10 lines of tcpdump log for debugging:")
            sample_cmd = 'head -10 %s' % tcpdump_log_file
            try:
                sample_output = remote_session.cmd_output(sample_cmd)
                test.log.debug("Sample log content:\n%s", sample_output)
            except Exception:
                test.log.debug("Could not read sample log content")

            test.fail('Captured packet on the remote host does not'
                     ' match the expectation')

        test.log.debug("UDP transfer test completed successfully")

    finally:
        test.log.debug("Clean up env")
        if vm_session is not None:
            if guest_os_type == 'linux':
                vm_session.cmd_status('rm -rf /home/netperf_log*')
            else:
                vm_session.cmd_status('del netperf_log*')

        if remote_session is not None:
            # Stop tcpdump
            remote_session.cmd_status("pkill tcpdump")
            # Stop netserver
            remote_session.cmd_status("pkill netserver")
            # Remove log file
            remote_session.cmd_status(f"rm -f {tcpdump_log}")
            if tcpdump_log_file is not None:
                remote_session.cmd_status(f"rm -f {tcpdump_log_file}")
            remote_session.close()

        if host_iface is not None:
            utils_net.delete_linux_bridge_tmux(bridge_name, host_iface)
        network_base.delete_tap(tap_name)
        backup_xml.sync()
