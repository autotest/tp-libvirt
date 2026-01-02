# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Nannan Li<nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


import os
import re

from avocado.utils import process

from virttest import data_dir
from virttest import utils_misc
from virttest import utils_package
from virttest import remote
from virttest.libvirt_xml import vm_xml


def run(test, params, env):
    """
    Test UDP packet transfer from VM to remote host.

    Steps:
    1. Setup VM and remote host environment
    2. Install and configure netperf on remote host and VM
    3. Start packet capture on remote host
    4. Run UDP transfer test from VM to remote host
    5. Verify packets are captured correctly
    6. Clean up environment

    """

    def disable_firewall(session, params, os_type="linux"):
        """
        Disable firewall on the target system.
        :param session: Session object for executing commands
        :param params: Params object
        :param os_type: OS type ("linux" or "windows")
        """
        firewall_cmd = params.get("firewall_cmd")

        if os_type == "linux":
            status = session.cmd_status(firewall_cmd)
            if status != 0:
                test.log.debug("Failed to disable firewall or already disabled")
        else:
            try:
                session.cmd(firewall_cmd, timeout=30)
                test.log.debug("Disabled legacy Windows firewall")
                session.cmd('netsh advfirewall set allprofiles state off', timeout=30)
                test.log.debug("Disabled Windows advanced firewall")
            except Exception as e:
                test.log.debug("Could not disable Windows firewall (may not exist or already disabled): %s", e)

    def transfer_netperf(guest_session):
        """
        Transfer and install netperf.

        :param guest_session: Guest session object
        """
        # Get OS-specific configuration (parameters are OS-aware from config)
        netperf_source = os.path.join(data_dir.get_deps_dir("netperf"), params.get('netperf_pkg'))
        netperf_dest = params.get("netperf_install_dest")
        install_cmd = params.get("netperf_install_cmd")
        verify_cmd = params.get("netperf_verify_cmd")

        test.log.debug('Transfer netperf file to guest')
        vm.copy_files_to(netperf_source, netperf_dest, timeout=300)
        test.log.debug("Successfully transferred netperf file to guest VM")

        # Install netperf (Linux only - Windows is just copy)
        if install_cmd:
            test.log.debug('Install netperf in guest')
            guest_session.cmd(install_cmd, timeout=600)

        test.log.debug('Verify netperf installation')
        status, output = guest_session.cmd_status_output(verify_cmd)
        if status == 0 and 'netperf' in output.lower():
            test.log.debug('Netperf installation verified successfully')
        else:
            test.fail('Failed to install/transfer netperf to guest: status=%s, output=%s' % (status, output))

    def run_netserver(host_session):
        """
        Install and run netserver.

        :param host_session: Host session object for executing commands
        """
        test.log.debug('Install netserver on remote host firstly')
        remote_ip = params.get("remote_ip")
        remote_user = params.get("remote_user", "root")
        remote_passwd = params.get("remote_pwd")

        test.log.debug('Scp netperf to remote host.')
        netperf_pkg_remote = params.get("netperf_pkg_remote")
        netperf_linux_path = os.path.join(data_dir.get_deps_dir("netperf"), netperf_pkg_remote)
        utils_misc.make_dirs(os.path.dirname(netperf_linux_path), host_session)
        remote.copy_files_to(remote_ip, 'scp', remote_user, remote_passwd,
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
        Run netperf UDP test.

        :param guest_session: Guest session object
        :param dst_host_ip: Destination host IP address
        :param guest_os_type: Guest OS type ("linux" or "windows")
        :param packet_size: UDP packet size for netperf test
        :param netperf_install_path: Installation path for Windows netperf
        :return: netperf log filename
        """
        netperf_cmd = params.get("netperf_cmd")

        netperf_log = 'netperf_log_%s' % utils_misc.generate_random_string(6)
        if guest_os_type == 'linux':
            guest_session.cmd(netperf_cmd % (dst_host_ip, packet_size, netperf_log))
        else:
            guest_session.cmd('cd "%s" && %s' % (netperf_install_path, netperf_cmd % (dst_host_ip, packet_size, netperf_log)))
        return netperf_log

    def check_netperf_log(guest_session, netperf_log, guest_os_type, packet_size, netperf_install_path='C:\\Program Files'):
        """
        Check netperf log results.
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

        # Check netperf log content
        data_match = params.get("data_match")
        viewlog_cmd = params.get("viewlog_cmd") % netperf_log

        output = guest_session.cmd_output(viewlog_cmd)
        if data_match and str(packet_size) in output:
            test.log.debug('The log of netperf checking is PASS')
        else:
            test.fail("The log of netperf isn't right:%s" % output)
        return netperf_log

    def verify_packet_capture(remote_session, vm_ip, remote_ip, packet_size, tcpdump_log_file):
        """
        Verify UDP packets are captured in tcpdump log file.

        :param remote_session: Remote session object
        :param vm_ip: VM IP address
        :param remote_ip: Remote host IP address
        :param packet_size: Expected UDP packet size
        :param tcpdump_log_file: Path to tcpdump log file
        :raises: test.fail if packets not found after debugging
        """
        # Search for expected UDP packets in tcpdump log
        expected_pattern = r'IP %s\.[0-9]+ > %s\.[0-9]+: UDP, length %s' % (vm_ip, remote_ip, packet_size)
        grep_cmd = 'grep -E "%s" %s | head -5' % (expected_pattern, tcpdump_log_file)

        test.log.debug("Searching for pattern: %s", expected_pattern)
        test.log.debug("vm_ip: %s, remote_ip: %s, packet_size: %s", vm_ip, remote_ip, packet_size)

        try:
            grep_output = remote_session.cmd_output(grep_cmd).strip()
            if grep_output:
                test.log.debug("Found matching packets:")
                for line in grep_output.split('\n')[:3]:  # Show first 3 matches
                    test.log.debug("  %s", line.strip())
                test.log.debug('Packet capture verification successful')
                return

        except Exception as e:
            test.log.debug("Grep search failed: %s", str(e))

        # No packets found - show debug info and fail
        test.log.debug("No matching packets found. Showing tcpdump log sample:")
        try:
            sample_output = remote_session.cmd_output('head -10 %s' % tcpdump_log_file)
            test.log.debug("Sample log content:\n%s", sample_output)
        except Exception as e:
            test.log.debug("Could not read log file: %s", str(e))

        test.fail('No UDP packets captured matching expected pattern')

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    netperf_version = params.get("netperf_version")

    # Handle variable substitution in paths
    remote_ip = params.get("migrate_dest_host")
    remote_user = params.get("remote_user", "root")
    remote_passwd = params.get("remote_pwd")
    guest_passwd = params.get("password")
    guest_os_type = params.get("os_type", "linux")
    packet_size = params.get("udp_packet_size")
    del_log_cmd = params.get("del_log_cmd")
    netperf_install_path = params.get("netperf_install_path")
    tcpdump_log_file = '/tmp/UDP_tcpdump.log'

    netperf_pkg = params.get('netperf_pkg')
    netperf_linux_path = os.path.join(data_dir.get_deps_dir("netperf"), netperf_pkg)

    remote_session = remote.remote_login(
        "ssh", remote_ip, "22", remote_user, remote_passwd, r"[\#\$]\s*$")

    def run_test():
        """
        Execute the main test steps for UDP packet transfer verification.

        This function performs the complete test workflow:
        1. Boot up the guest VM and get its IP address
        2. Start netperf server on the remote host
        3. Start packet capture (tcpdump) on remote host
        4. Transfer and install netperf on the guest
        5. Run netperf UDP test from guest to remote host
        6. Verify packets were captured correctly on remote host
        """
        test.log.info("TEST_STEP1: Boot up a guest on src host")
        if not vm.is_alive():
            vm.start()
        vm_session = vm.wait_for_login()
        test.log.debug("Guest xml:\n%s", vm_xml.VMXML.new_from_dumpxml(vm_name))

        vm_ip = vm.get_address()
        test.log.debug("VM IP: %s and Remote host: %s", vm_ip, remote_ip)

        test.log.info("TEST_STEP2: Start netperf server on remote host")
        run_netserver(remote_session)

        test.log.debug("TEST_STEP3: Capture the packet from guest")
        if not utils_package.package_install('tcpdump', session=remote_session):
            test.fail("tcpdump package install failed")
        remote_session.sendline('tcpdump -n udp and src %s > %s 2>&1 &'
                                % (vm_ip, tcpdump_log_file))

        test.log.info("TEST_STEP4: Transfer and Run netperf in the guest")
        if guest_os_type == 'linux':
            process.run("yum -y install sshpass", ignore_status=True)

            # Generate SSH key if it doesn't exist
            process.run('mkdir -p /root/.ssh', ignore_status=True)
            if not os.path.exists('/root/.ssh/id_rsa'):
                process.run('ssh-keygen -t rsa -b 2048 -f /root/.ssh/id_rsa -N ""', ignore_status=True)

            process.run('sshpass -p %s ssh-copy-id -o "StrictHostKeyChecking no" -i '
                        '/root/.ssh/id_rsa.pub root@%s' % (guest_passwd, vm_ip), ignore_status=True)

        disable_firewall(vm_session, params, guest_os_type)
        disable_firewall(remote_session, params, "linux")

        transfer_netperf(vm_session)

        netperf_log = run_netperf(vm_session, remote_ip, guest_os_type, packet_size, netperf_install_path)
        check_netperf_log(vm_session, netperf_log, guest_os_type, packet_size, netperf_install_path)

        test.log.info("TEST_STEP5: Verify packet capture")
        verify_packet_capture(remote_session, vm_ip, remote_ip, packet_size, tcpdump_log_file)
        vm_session.close()

    def teardown():

        test.log.info("TEST_TEARDOWN: Clean up env.")
        vm_session = vm.wait_for_login()
        vm_session.cmd_status(del_log_cmd)
        vm_session.close()

        remote_session.cmd_status("pkill tcpdump; pkill netserver")
        remote_session.cmd_status(f"rm -f {tcpdump_log_file}")
        remote_session.close()

    try:
        run_test()

    finally:
        teardown()
