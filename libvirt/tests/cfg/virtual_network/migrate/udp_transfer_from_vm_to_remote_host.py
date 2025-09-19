import aexpect
import os
import re
import logging as log

from avocado.utils import process

from virttest import virsh
from virttest import utils_misc
from virttest import utils_net
from virttest import utils_package
from virttest import data_dir
from virttest import remote

from provider.virtual_network import utils_win


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
                logging.warning("Failed to disable firewall or already disabled")
        else:
            output = session.cmd_output(firewall_cmd)
            test.log.debug("Windows firewall disabled: %s", output)


    def transfer_netperf(host_session, guest_session, guest_os_type, src_guest_ip, src_host_ip, password, netperf_install_path='C:\\Program Files'):
        """
        Transfer and install netperf using tp-libvirt session methods.

        :param host_session: Host session object
        :param guest_session: Guest session object
        :param guest_os_type: Guest OS type ("linux" or "windows")
        :param src_guest_ip: Source guest IP address
        :param src_host_ip: Source host IP address
        :param password: Password for authentication
        :param netperf_install_path: Installation path for Windows netperf
        """
        if guest_os_type == 'linux':
            logging.info('Transfer netperf tar to guest')
            list_cmd = 'ls -ld /home/netperf-2.7.1'
            status, output = guest_session.cmd_status_output(list_cmd)
            if r'No such file or directory' not in output:
                guest_session.cmd('rm -rf /home/netperf-2.7.1')

            host_session.cmd('scp %s root@%s:/home' % (netperf_linux_path, src_guest_ip))

            logging.info('Install netperf in guest firstly')
            install_cmd = ('tar jxf /home/netperf-2.7.1.tar.bz2 -C /home/ && '
                          'cd /home/netperf-2.7.1 && ./autogen.sh && '
                          './configure && make && make install')
            guest_session.cmd(install_cmd, timeout=600)

            status, output = guest_session.cmd_status_output(list_cmd)
            if r'No such file or directory' in output:
                test.fail('Fail to install netperf in guest')
        else:
            utils_win.pscp_file(guest_session, netperf_windows_path,
                                netperf_install_path, src_host_ip, 'root', password)
            check_installed_cmd = 'dir "%s"|findstr /I netperf' % netperf_install_path
            output = guest_session.cmd_output(check_installed_cmd)
            if 'netperf' in output:
                logging.info('Success to transfer netperf.exe from '
                                'src host to guest')
            else:
                test.fail('Fail to transfer netperf.exe from '
                               'src host to guest')


    def run_netserver(host_session):
        """
        Install and run netserver.

        :param host_session: Host session object for executing commands
        """
        logging.info('Install netserver on host firstly')
        list_cmd = 'ls -ld /home/netperf-2.7.1'
        if r'No such file or directory' not in \
                host_session.cmd_output(list_cmd):
            host_session.cmd_output('rm -rf /home/netperf-2.7.1')
        install_cmd = 'tar jxf %s -C /home/ && ' \
                      'cd /home/netperf-2.7.1  && ./autogen.sh  && ' \
                      './configure && make && make install' % netperf_linux_path
        host_session.cmd_output(cmd=install_cmd)
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
                logging.info('Finish to execute netperf in guest')
            else:
                test.fail('Timeout to execute netperf in guest under 120s')
        else:
            cmd = 'tasklist /FI "imagename eq netperf.exe"'
            guest_session.cmd('cd %s' % netperf_install_path)
            if utils_misc.wait_for(lambda: not re.search(
                    r'netperf.exe', guest_session.cmd_output(cmd)), 120, step=3.0):
                logging.info('Finish to execute netperf in guest')
            else:
                test.fail('Timeout to execute netperf in guest under 120s')
        data_match = 'MIGRATED UDP STREAM TEST from'
        viewlog_cmd = 'cat /home/%s' % netperf_log
        if guest_os_type == 'windows':
            viewlog_cmd = 'type %s' % netperf_log
        output = guest_session.cmd_output(viewlog_cmd)
        if data_match and str(packet_size) in output:
            logging.info('The log of netperf is right')
        else:
            test.fail("The log of netperf isn't right")
        return netperf_log


    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    netperf_linux_path = os.path.join(os.path.dirname(__file__), params.get('netperf_linux'))
    netperf_windows_path = os.path.join(os.path.dirname(__file__), params.get('netperf_windows'))

    # Migration parameters
    remote_host = params.get("remote_host")
    remote_user = params.get("remote_user", "root") 
    remote_passwd = params.get("remote_passwd")
    guest_os_type = params.get("guest_os_type", "linux")
    packet_size = params.get("udp_packet_size")
    tcpdump_log = "/tmp/udp_capture.log"
    local_host_session = aexpect.ShellSession('ls')

    try:
        test.log.debug("TEST_STEP1: Boot up a guest on src host")
        if not vm.is_alive():
            vm.start()
        vm_session = vm.wait_for_login()

        vm_ip = vm.get_address()
        logging.info("VM IP: %s", vm_ip)
        logging.info("Remote host: %s", remote_host)
        remote_session = remote.remote_login("ssh", remote_host, "22",
                                           remote_user, remote_passwd, r"[\#\$]\s*$")

        # Install and run netserver on remote host
        test.log.debug("TEST_STEP2: Start netperf server on remote host")
        # Setup SSH keys for Linux guests
        if guest_os_type == 'linux':
            password = params.get('guest_passwd', 'kvmautotest')
            remote_session.cmd_output('sshpass -p %s ssh-copy-id -o '
                                     '"StrictHostKeyChecking no" -i '
                                     '/root/.ssh/id_rsa.pub root@%s' % (password, vm_ip))
        run_netserver(remote_session)

        # Start packet capture on remote host
        test.log.debug("TEST_STEP3: Capture the packet from guest")
        if not utils_package.package_install('tcpdump', session=remote_session):
            test.fail("tcpdump package install failed")
        tcpdump_log_file = os.path.join(data_dir.get_tmp_dir(), 'UDP_migration_tcpdump.log')
        remote_session.sendline('tcpdump -n udp and src %s > %s 2>&1'
                                % (vm_ip, tcpdump_log_file))


        test.log.debug("TEST_STEP4: Run netperf client command in the guest")
        # Stop firewall for guest
        if guest_os_type == 'linux':
            process.run('sshpass -p %s ssh-copy-id -o "StrictHostKeyChecking no" -i '
                                             '/root/.ssh/id_rsa.pub root@%s' %
                                             (password, vm_ip))
        disable_firewall(vm_session, params, guest_os_type)
        disable_firewall(remote_session, params, "linux")

        # Transfer and install netperf in guest via refactored function
        password = params.get('guest_passwd', 'kvmautotest')
        transfer_netperf(local_host_session, vm_session, guest_os_type, vm_ip, remote_host, password)

        try:
            netperf_log = run_netperf(vm_session, remote_host, guest_os_type, packet_size)
            check_netperf_log(vm_session, netperf_log, guest_os_type, packet_size)

        finally:
            if guest_os_type == 'linux':
                vm_session.sendline('rm -rf /home/netperf_log*')
            else:
                vm_session.sendline('del netperf_log*')

        test.log.debug("TEST_STEP5: Verify packet capture")
        utils_misc.wait_for(lambda: True, 5)  # Wait for packets to be written
        with open(tcpdump_log_file) as f:
            for line in f:
                logging.info(line.strip())
                if re.search('IP %s\\.\\d+ > %s\\.\\d+: UDP, length %s'
                            % (vm_ip, remote_host, packet_size), line):
                    logging.info('Captured packet: %s on the remote host '
                                'matches the expectation' % line.strip())
                    break
            else:
                test.fail('Captured packet on the remote host does not'
                         ' match the expectation')

        test.log.debug("UDP transfer test completed successfully")

    finally:
        try:
            if 'vm_session' in locals():
                if guest_os_type == 'linux':
                    vm_session.cmd_status('rm -rf /home/netperf_log*')
                else:
                    vm_session.cmd_status('del netperf_log*')
        except Exception as e:
            logging.warning("Failed to cleanup netperf logs: %s", e)

        # Cleanup
        try:
            if 'remote_session' in locals():
                # Stop tcpdump
                remote_session.cmd_status("pkill tcpdump")
                # Stop netserver
                remote_session.cmd_status("pkill netserver")
                # Remove log file
                remote_session.cmd_status(f"rm -f {tcpdump_log}")
                remote_session.cmd_status(f"rm -f {tcpdump_log_file}")
                remote_session.close()
        except Exception as e:
            logging.warning("Cleanup failed: %s", e)
        
        try:
            if 'vm_session' in locals():
                vm_session.close()
        except Exception as e:
            logging.warning("VM session cleanup failed: %s", e)
