import os
import logging
import re
import uuid
import shutil
import time
import tempfile

from virttest import data_dir
from virttest import utils_misc
from virttest import utils_package
from virttest import utils_sasl
from virttest import utils_v2v
from virttest import virsh
from virttest import remote
from virttest.utils_test import libvirt
from virttest.utils_v2v import params_get
from avocado.utils import process
from avocado.utils import download
from aexpect.exceptions import ShellProcessTerminatedError, ShellTimeoutError, ShellStatusError

from provider.v2v_vmcheck_helper import VMChecker
from provider.v2v_vmcheck_helper import check_json_output
from provider.v2v_vmcheck_helper import check_local_output


def run(test, params, env):
    """
    Convert specific esx guest
    """
    V2V_UNSUPPORT_RHEV_APT_VER = "[virt-v2v-1.43.3-4.el9,)"

    for v in list(params.values()):
        if "V2V_EXAMPLE" in v:
            test.cancel("Please set real value for %s" % v)
    if utils_v2v.V2V_EXEC is None:
        raise ValueError('Missing command: virt-v2v')
    enable_legacy_cp = params.get(
        "enable_legacy_crypto_policies",
        'no') == 'yes'
    version_requried = params.get("version_requried")
    unprivileged_user = params_get(params, 'unprivileged_user')
    vpx_hostname = params.get('vpx_hostname')
    vpx_passwd = params.get("vpx_password")
    esxi_host = esx_ip = params.get('esx_hostname')
    vpx_dc = params.get('vpx_dc')
    vm_name = params.get('main_vm')
    output_mode = params.get('output_mode')
    pool_name = params.get('pool_name', 'v2v_test')
    pool_type = params.get('pool_type', 'dir')
    pool_target = params.get('pool_target_path', 'v2v_pool')
    pvt = libvirt.PoolVolumeTest(test, params)
    v2v_timeout = int(params.get('v2v_timeout', 1200))
    v2v_cmd_timeout = int(params.get('v2v_cmd_timeout', 18000))
    v2v_opts = '-v -x' if params.get('v2v_debug',
                                     'on') in ['on', 'force_on'] else ''
    if params.get("v2v_opts"):
        # Add a blank by force
        v2v_opts += ' ' + params.get("v2v_opts")
    status_error = 'yes' == params.get('status_error', 'no')
    address_cache = env.get('address_cache')
    checkpoint = params.get('checkpoint', '').split(',')
    skip_vm_check = params.get('skip_vm_check', 'no')
    skip_reason = params.get('skip_reason')
    error_list = []
    remote_host = vpx_hostname
    # For VDDK
    input_transport = params.get("input_transport")
    vddk_libdir = params.get('vddk_libdir')
    # nfs mount source
    vddk_libdir_src = params.get('vddk_libdir_src')
    vddk_thumbprint = params.get('vddk_thumbprint')
    src_uri_type = params.get('src_uri_type')
    esxi_password = params.get('esxi_password')
    json_disk_pattern = params.get('json_disk_pattern')
    # For construct rhv-upload option in v2v cmd
    output_method = params.get("output_method")
    rhv_upload_opts = params.get("rhv_upload_opts")
    storage_name = params.get('storage_name')
    os_pool = os_storage = params.get('output_storage', 'default')
    # for get ca.crt file from ovirt engine
    rhv_passwd = params.get("rhv_upload_passwd")
    rhv_passwd_file = params.get("rhv_upload_passwd_file")
    ovirt_engine_passwd = params.get("ovirt_engine_password")
    ovirt_hostname = params.get("ovirt_engine_url").split(
        '/')[2] if params.get("ovirt_engine_url") else None
    ovirt_ca_file_path = params.get("ovirt_ca_file_path")
    local_ca_file_path = params.get("local_ca_file_path")
    os_version = params.get('os_version')
    os_type = params.get('os_type')
    virtio_win_path = params.get('virtio_win_path')
    # qemu-guest-agent path in virtio-win or rhv-guest-tools-iso
    qa_path = params.get('qa_path')
    # download url of qemu-guest-agent
    qa_url = params.get('qa_url')
    v2v_sasl = None
    # default values for v2v_cmd
    auto_clean = True
    cmd_only = False
    cmd_has_ip = 'yes' == params.get('cmd_has_ip', 'yes')
    interaction_run = 'yes' == params.get('interaction_run', 'no')

    def log_fail(msg):
        """
        Log error and update error list
        """
        logging.error(msg)
        error_list.append(msg)

    def check_device_exist(check, virsh_session_id):
        """
        Check if device exist after convertion
        """
        xml = virsh.dumpxml(vm_name, session_id=virsh_session_id).stdout
        if check == 'cdrom':
            if "device='cdrom'" not in xml:
                log_fail('CDROM no longer exists')

    def check_vmtools(vmcheck, check):
        """
        Check whether vmware tools packages have been removed,
        or vmware-tools service has stopped

        :param vmcheck: VMCheck object for vm checking
        :param check: Checkpoint of different cases
        :return: None
        """
        if "service" not in check:
            logging.info('Check if packages been removed')
            pkgs = vmcheck.session.cmd('rpm -qa').strip()
            removed_pkgs = params.get('removed_pkgs').strip().split(',')
            if not removed_pkgs:
                test.error('Missing param "removed_pkgs"')
            for pkg in removed_pkgs:
                if pkg in pkgs:
                    log_fail('Package "%s" not removed' % pkg)
        else:
            logging.info('Check if service stopped')
            vmtools_service = params.get('service_name')
            status = utils_misc.get_guest_service_status(
                vmcheck.session, vmtools_service)
            logging.info('Service %s status: %s', vmtools_service, status)
            if status != 'inactive':
                log_fail('Service "%s" is not stopped' % vmtools_service)

    def check_modprobe(vmcheck):
        """
        Check whether content of /etc/modprobe.conf meets expectation
        """
        content = vmcheck.session.cmd('cat /etc/modprobe.conf').strip()
        logging.debug(content)
        cfg_content = params.get('cfg_content')
        if not cfg_content:
            test.error('Missing content for search')
        logging.info('Search "%s" in /etc/modprobe.conf', cfg_content)
        pattern = r'\s+'.join(cfg_content.split())
        if not re.search(pattern, content):
            log_fail('Not found "%s"' % cfg_content)

    def check_device_map(vmcheck):
        """
        Check if the content of device.map meets expectation.
        """
        logging.info(vmcheck.session.cmd('fdisk -l').strip())
        device_map = params.get('device_map_path')
        content = vmcheck.session.cmd('cat %s' % device_map)
        logging.debug('Content of device.map:\n%s', content)
        logging.info('Found device: %d', content.count('/dev/'))
        logging.info('Found virtio device: %d', content.count('/dev/vd'))
        if content.count('/dev/') != content.count('/dev/vd'):
            log_fail('Content of device.map not correct')
        else:
            logging.info('device.map has been remaped to "/dev/vd*"')

    def check_resume_swap(vmcheck):
        """
        Check the content of grub files meet expectation.
        """
        if os_version == 'rhel7':
            chkfiles = [
                '/etc/default/grub',
                '/boot/grub2/grub.cfg',
                '/etc/grub2.cfg']
        if os_version == 'rhel6':
            chkfiles = ['/boot/grub/grub.conf', '/etc/grub.conf']
        for file_i in chkfiles:
            status, content = vmcheck.run_cmd('cat %s' % file_i)
            if status != 0:
                log_fail('%s does not exist' % file_i)
            resume_dev_count = content.count('resume=/dev/')
            if resume_dev_count == 0 or resume_dev_count != content.count(
                    'resume=/dev/vd'):
                reason = 'Maybe the VM\'s swap pariton is lvm'
                log_fail(
                    'Content of %s is not correct or %s' %
                    (file_i, reason))

        content = vmcheck.session.cmd('cat /proc/cmdline')
        logging.debug('Content of /proc/cmdline:\n%s', content)
        if 'resume=/dev/vd' not in content:
            log_fail('Content of /proc/cmdline is not correct')

    def check_rhev_file_exist(vmcheck):
        """
        Check if rhev files exist
        """
        file_path = {
            'rhev-apt.exe': r'C:\rhev-apt.exe',
            'rhsrvany.exe': r'"C:\Program Files\Guestfs\Firstboot\rhsrvany.exe"'}
        # rhev-apt.ext is removed on rhel9
        if utils_v2v.multiple_versions_compare(V2V_UNSUPPORT_RHEV_APT_VER):
            file_path.pop('rhev-apt.exe')
        for key in file_path:
            status = vmcheck.session.cmd_status('dir %s' % file_path[key])
            if status == 0:
                logging.info('%s exists' % key)
            else:
                log_fail('%s does not exist after convert to rhv' % key)

    def check_file_architecture(vmcheck):
        """
        Check the 3rd party module info

        :param vmcheck: VMCheck object for vm checking
        """
        content = vmcheck.session.cmd('uname -r').strip()
        status = vmcheck.session.cmd_status(
            'rpm -qf /lib/modules/%s/fileaccess/fileaccess_mod.ko ' %
            content)
        if status == 0:
            log_fail('3rd party module info is not correct')
        else:
            logging.info(
                'file /lib/modules/%s/fileaccess/fileaccess_mod.ko is not owned by any package' %
                content)

    def check_windows_signature(vmcheck, full_name):
        """
        Check signature of a file in windows VM

        :param vmcheck: VMCheck object for vm checking
        :param full_name: a file's full path name
        """
        logging.info(
            'powershell or signtool needs to be installed in guest first')

        cmds = [
            ('powershell "Get-AuthenticodeSignature %s | format-list"' %
             full_name,
             r'SignerCertificate.*?Not After](.*?)\[Thumbprint',
             '%m/%d/%Y %I:%M:%S %p'),
            ('signtool verify /v %s' %
             full_name,
             r'Issued to: Red Hat.*?Expires:(.*?)SHA1 hash',
             '')]
        for cmd, ptn, fmt in cmds:
            _, output = vmcheck.run_cmd(cmd)
            if re.search(ptn, output, re.S):
                expire_time = re.search(ptn, output, re.S).group(1).strip()
                if fmt:
                    expire_time = time.strptime(expire_time, fmt)
                else:
                    expire_time = time.strptime(expire_time)
                if time.time() > time.mktime(expire_time):
                    test.fail("Signature of '%s' has expired" % full_name)
                return
        # Get here means the guest doesn't have powershell or signtool
        test.error("Powershell or Signtool must be installed in guest")

    def check_windows_vmware_tools(vmcheck):
        """
        Check vmware tools is uninstalled in VM

        :param vmcheck: VMCheck object for vm checking
        """
        def _get_vmware_info(cmd):
            _, res = vmcheck.run_cmd(cmd)
            if res and not re.search('vmtools', res, re.I):
                return True
            return False

        cmds = ['tasklist', 'sc query vmtools']
        for cmd in cmds:
            res = utils_misc.wait_for(
                lambda: _get_vmware_info(cmd), 600, step=30)
            if not res:
                test.fail("Failed to verification vmtools uninstallation")

    def check_windows_service(vmcheck, service_name):
        """
        Check service in VM

        :param vmcheck: VMCheck object for vm checking
        :param service_name: a service's name
        """
        try:
            res = utils_misc.wait_for(
                lambda: re.search(
                    'running',
                    vmcheck.get_service_info(service_name),
                    re.I),
                600,
                step=30)
        except (ShellProcessTerminatedError, ShellStatusError):
            # Windows guest may reboot after installing qemu-ga service
            logging.debug('Windows guest is rebooting')
            if vmcheck.session:
                vmcheck.session.close()
                vmcheck.session = None
            # VM boots up is extremly slow when all testing in running on
            # rhv server simultaneously, so set timeout to 1200.
            vmcheck.create_session(timeout=1200)
            res = utils_misc.wait_for(
                lambda: re.search(
                    'running',
                    vmcheck.get_service_info(service_name),
                    re.I),
                600,
                step=30)

        if not res:
            test.fail('Not found running %s service' % service_name)

    def check_linux_ogac(vmcheck):
        """
        Check qemu-guest-agent service in VM

        :param vmcheck: VMCheck object for vm checking
        """
        def get_pkgs(pkg_path):
            """
            Get all qemu-guest-agent pkgs
            """
            pkgs = []
            for _, _, files in os.walk(pkg_path):
                for file_name in files:
                    pkgs.append(file_name)
            return pkgs

        def get_pkg_version_vm():
            """
            Get qemu-guest-agent version in VM
            """
            vender = vmcheck.get_vm_os_vendor()
            if vender in ['Ubuntu', 'Debian']:
                cmd = 'dpkg -l qemu-guest-agent'
            else:
                cmd = 'rpm -q qemu-guest-agent'
            _, output = vmcheck.run_cmd(cmd)

            pkg_ver_ptn = [r'qemu-guest-agent +[0-9]+:(.*?dfsg.*?) +',
                           r'qemu-guest-agent-(.*?)\.x86_64']

            for ptn in pkg_ver_ptn:
                if re.search(ptn, output):
                    return re.search(ptn, output).group(1)
            return ''

        if os.path.isfile(os.getenv('VIRTIO_WIN')):
            mount_point = utils_v2v.v2v_mount(
                os.getenv('VIRTIO_WIN'),
                'rhv_tools_setup_iso',
                fstype='iso9660')
            export_path = params['tmp_mount_point'] = mount_point
        else:
            export_path = os.getenv('VIRTIO_WIN')

        qemu_guest_agent_dir = os.path.join(export_path, qa_path)
        all_pkgs = get_pkgs(qemu_guest_agent_dir)
        logging.debug('The installing qemu-guest-agent is: %s' % all_pkgs)
        vm_pkg_ver = get_pkg_version_vm()
        logging.debug('qemu-guest-agent verion in vm: %s' % vm_pkg_ver)

        # Check the service status of qemu-guest-agent in VM
        status_ptn = r'Active: active \(running\)|qemu-ga \(pid +[0-9]+\) is running'
        cmd = 'service qemu-ga status;systemctl status qemu-guest-agent;systemctl status qemu-ga*'
        _, output = vmcheck.run_cmd(cmd)

        if not re.search(status_ptn, output):
            log_fail('qemu-guest-agent service exception')

    def check_ubuntools(vmcheck):
        """
        Check open-vm-tools, ubuntu-server in VM

        :param vmcheck: VMCheck object for vm checking
        """
        logging.info('Check if open-vm-tools service stopped')
        status = utils_misc.get_guest_service_status(
            vmcheck.session, 'open-vm-tools')
        logging.info('Service open-vm-tools status: %s', status)
        if status != 'inactive':
            log_fail('Service open-vm-tools is not stopped')
        else:
            logging.info('Check if the ubuntu-server exist')
            content = vmcheck.session.cmd('dpkg -s ubuntu-server')
            if 'install ok installed' in content:
                logging.info('ubuntu-server has not been removed.')
            else:
                log_fail('ubuntu-server has been removed')

    def global_pem_setup(f_pem):
        """
        Setup global rhv server ca

        :param f_pem: ca file path
        """
        ca_anchors_dir = '/etc/pki/ca-trust/source/anchors'
        shutil.copy(f_pem, ca_anchors_dir)
        process.run('update-ca-trust extract', shell=True)
        os.unlink(os.path.join(ca_anchors_dir, os.path.basename(f_pem)))

    def global_pem_cleanup():
        """
        Cleanup global rhv server ca
        """
        process.run('update-ca-trust extract', shell=True)

    def find_net(bridge_name):
        """
        Find which network use specified bridge

       :param bridge_name: bridge name you want to find
        """
        net_list = virsh.net_state_dict(only_names=True)
        net_name = ''
        if len(net_list):
            for net in net_list:
                net_info = virsh.net_info(net).stdout.strip()
                search = re.search(r'Bridge:\s+(\S+)', net_info)
                if search:
                    if bridge_name == search.group(1):
                        net_name = net
        else:
            logging.info('Conversion server has no network')
        return net_name

    def destroy_net(net_name):
        """
        destroy network in conversion server
        """
        if virsh.net_state_dict()[net_name]['active']:
            logging.info("Remove network %s in conversion server", net_name)
            virsh.net_destroy(net_name)
            if virsh.net_state_dict()[net_name]['autostart']:
                virsh.net_autostart(net_name, "--disable")
        output = virsh.net_list("--all").stdout.strip()
        logging.info(output)

    def start_net(net_name):
        """
        start network in conversion server
        """
        logging.info("Recover network %s in conversion server", net_name)
        virsh.net_autostart(net_name)
        if not virsh.net_state_dict()[net_name]['active']:
            virsh.net_start(net_name)
        output = virsh.net_list("--all").stdout.strip()
        logging.info(output)

    def check_static_ip_conf(vmcheck):
        """
        Check static IP configuration in VM

        :param vmcheck: VMCheck object for vm checking
        """
        def _static_ip_check():
            cmd = 'ipconfig /all'
            _, output = vmcheck.run_cmd(cmd, debug=False)
            v2v_cmd = params_get(params, 'v2v_command')
            # --mac 00:50:56:ac:7a:4d:ip:192.168.1.2,192.168.1.1,22,192.168.1.100,10.73.2.108,10.66.127.10'
            mac_ip_pattern = '--mac (([0-9a-zA-Z]{2}:){6})ip:([0-9,.]+)'
            ip_config_list = re.search(mac_ip_pattern, v2v_cmd).group(3)
            mac_addr = re.search(mac_ip_pattern, v2v_cmd).group(1)[
                0:-1].upper().replace(':', '-')
            eth_adapter_ptn = r'Ethernet adapter Ethernet.*?NetBIOS over Tcpip'

            try:
                ipconfig = [
                    v for v in re.findall(
                        eth_adapter_ptn,
                        output,
                        re.S) if mac_addr in v][0]
            except IndexError:
                return False

            for i, value in enumerate(ip_config_list.split(',')):
                if not value:
                    continue
                # IP address
                if i == 0:
                    ip_addr = r'IPv4 Address.*?: %s' % value
                    if not re.search(ip_addr, ipconfig, re.S):
                        logging.debug('Found IP addr failed')
                        return False
                # Default gateway
                if i == 1:
                    ip_gw = r'Default Gateway.*?: .*?%s' % value
                    if not re.search(ip_gw, ipconfig, re.S):
                        logging.debug('Found Gateway failed')
                        return False
                # Subnet mask
                if i == 2:
                    # convert subnet mask to cidr
                    bin_mask = '1' * int(value) + '0' * (32 - int(value))
                    cidr = '.'.join(
                        [str(int(bin_mask[i * 8:i * 8 + 8], 2)) for i in range(4)])
                    sub_mask = r'Subnet Mask.*?: %s' % cidr
                    if not re.search(sub_mask, ipconfig, re.S):
                        logging.debug('Found subnet mask failed')
                        return False
                # DNS server list
                if i >= 3:
                    dns_server = r'DNS Servers.*?:.*?%s' % value
                    if not re.search(dns_server, ipconfig, re.S):
                        logging.debug('Found DNS Server failed')
                        return False
            return True

        try:
            vmcheck.create_session()
            res = utils_misc.wait_for(_static_ip_check, 1800, step=300)
        except (ShellTimeoutError, ShellProcessTerminatedError):
            logging.debug(
                'Lost connection to windows guest, the static IP may take effect')
            if vmcheck.session:
                vmcheck.session.close()
                vmcheck.session = None
            vmcheck.create_session()
            res = utils_misc.wait_for(_static_ip_check, 300, step=30)
        vmcheck.run_cmd('ipconfig /all')  # debug msg
        if not res:
            test.fail('Checking static IP configuration failed')

    def check_rhsrvany_checksums(vmcheck):
        """
        Check if MD5 and SHA1 of rhsrvany.exe are correct
        """
        def _get_expected_checksums(tool_exec, file):
            val = process.run(
                '%s %s' % (tool_exec, rhsrvany_path),
                shell=True).stdout_text.split()[0]

            if not val:
                test.error('Get checksume failed')
            logging.info('%s: Expect %s: %s', file, tool_exec, val)
            return val

        def _get_real_checksums(algorithm, file):
            certutil_cmd = r'certutil -hashfile "%s"' % file
            if algorithm == 'md5':
                certutil_cmd += ' MD5'

            res = vmcheck.session.cmd_output(certutil_cmd, safe=True)
            logging.debug('%s output:\n%s', certutil_cmd, res)

            val = res.strip().splitlines()[1].strip()
            logging.info('%s: Real %s: %s', file, algorithm, val)
            return val

        logging.info('Check md5 and sha1 of rhsrvany.exe')

        algorithms = {'md5': 'md5sum',
                      'sha1': 'sha1sum'}

        rhsrvany_path = r'/usr/share/virt-tools/rhsrvany.exe'
        rhsrvany_path_windows = r"C:\Program Files\Guestfs\Firstboot\rhsrvany.exe"

        for key, val in algorithms.items():
            expect_val = _get_expected_checksums(val, rhsrvany_path)
            real_val = _get_real_checksums(key, rhsrvany_path_windows)
            if expect_val == real_val:
                logging.info('%s are correct', key)
            else:
                test.fail('%s of rhsrvany.exe is not correct' % key)

    def check_result(result, status_error):
        """
        Check virt-v2v command result
        """
        def vm_check(status_error):
            """
            Checking the VM
            """
            if status_error:
                return

            if output_mode == 'json' and not check_json_output(params):
                test.fail('check json output failed')
            if output_mode == 'local' and not check_local_output(params):
                test.fail('check local output failed')
            if output_mode in ['null', 'json', 'local']:
                return

            # vmchecker must be put before skip_vm_check in order to clean up
            # the VM.
            vmchecker = VMChecker(test, params, env)
            params['vmchecker'] = vmchecker
            if skip_vm_check == 'yes':
                logging.info(
                    'Skip checking vm after conversion: %s' %
                    skip_reason)
                return

            if output_mode == 'rhev':
                if not utils_v2v.import_vm_to_ovirt(params, address_cache,
                                                    timeout=v2v_timeout):
                    test.fail('Import VM failed')
            elif output_mode == 'libvirt':
                virsh.start(vm_name, debug=True)

            # Check guest following the checkpoint document after convertion
            logging.info('Checking common checkpoints for v2v')
            if 'ogac' in checkpoint:
                # windows guests will reboot at any time after qemu-ga is
                # installed. The process cannot be controled. In order to
                # don't break vmchecker.run() process, It's better to put
                # check_windows_ogac before vmchecker.run(). Because in
                # check_windows_ogac, it waits until rebooting completes.
                vmchecker.checker.create_session()
                if os_type == 'windows':
                    services = ['qemu-ga']
                    if not utils_v2v.multiple_versions_compare(
                            V2V_UNSUPPORT_RHEV_APT_VER):
                        services.append('rhev-apt')
                    if 'rhv-guest-tools' in os.getenv('VIRTIO_WIN'):
                        services.append('spice-ga')
                    for ser in services:
                        check_windows_service(vmchecker.checker, ser)
                else:
                    check_linux_ogac(vmchecker.checker)
            if 'mac_ip' in checkpoint:
                check_static_ip_conf(vmchecker.checker)
            ret = vmchecker.run()
            if len(ret) == 0:
                logging.info("All common checkpoints passed")
            # Check specific checkpoints
            if 'ogac' in checkpoint and 'signature' in checkpoint:
                if not utils_v2v.multiple_versions_compare(
                        V2V_UNSUPPORT_RHEV_APT_VER):
                    check_windows_signature(vmchecker.checker, r'c:\rhev-apt.exe')
            if 'cdrom' in checkpoint:
                virsh_session = utils_sasl.VirshSessionSASL(params)
                virsh_session_id = virsh_session.get_id()
                check_device_exist('cdrom', virsh_session_id)
                virsh_session.close()
            if 'vmtools' in checkpoint:
                check_vmtools(vmchecker.checker, checkpoint)
            if 'modprobe' in checkpoint:
                check_modprobe(vmchecker.checker)
            if 'device_map' in checkpoint:
                check_device_map(vmchecker.checker)
            if 'resume_swap' in checkpoint:
                check_resume_swap(vmchecker.checker)
            if 'rhev_file' in checkpoint:
                check_rhev_file_exist(vmchecker.checker)
            if 'file_architecture' in checkpoint:
                check_file_architecture(vmchecker.checker)
            if 'ubuntu_tools' in checkpoint:
                check_ubuntools(vmchecker.checker)
            if 'vmware_tools' in checkpoint:
                check_windows_vmware_tools(vmchecker.checker)
            if 'without_default_net' in checkpoint:
                if virsh.net_state_dict()[net_name]['active']:
                    log_fail("Bridge virbr0 already started during conversion")
            if 'rhsrvany_checksum' in checkpoint:
                check_rhsrvany_checksums(vmchecker.checker)
            # Merge 2 error lists
            error_list.extend(vmchecker.errors)
            # Virtio drivers will not be installed without virtio-win setup
            if 'virtio_win_unset' in checkpoint:
                missing_list = params.get('missing').split(',')
                expect_errors = ['Not find driver: ' + x for x in missing_list]
                logging.debug('Expect errors: %s' % expect_errors)
                logging.debug('Actual errors: %s' % error_list)
                if set(error_list) == set(expect_errors):
                    error_list[:] = []
                else:
                    logging.error('Virtio drivers not meet expectation')

        utils_v2v.check_exit_status(result, status_error)
        output = result.stdout_text + result.stderr_text
        # VM or local output checking
        vm_check(status_error)
        # Check log size decrease option
        if 'log decrease' in checkpoint:
            nbdkit_option = r'nbdkit\.backend\.datapath=0'
            if not re.search(nbdkit_option, output):
                test.fail("checkpoint '%s' failed" % checkpoint)
        if 'block_dev' in checkpoint:
            if not os.path.exists(blk_dev_link):
                test.fail("checkpoint '%s' failed" % checkpoint)
        if 'fstrim_warning' in checkpoint:
            # Actually, fstrim has no relationship with v2v, it may be related
            # to kernel, this warning really doesn't matter and has no harm to
            # the convertion.
            V2V_FSTRIM_SUCESS_VER = "[virt-v2v-1.45.1-1.el9,)"
            if utils_v2v.multiple_versions_compare(V2V_FSTRIM_SUCESS_VER):
                params.update({'expect_msg': None})
        # Log checking
        log_check = utils_v2v.check_log(params, output)
        if log_check:
            log_fail(log_check)
        if len(error_list):
            test.fail('%d checkpoints failed: %s' %
                      (len(error_list), error_list))

    try:
        if version_requried and not utils_v2v.multiple_versions_compare(
                version_requried):
            test.cancel("Testing requries version: %s" % version_requried)

        # See man virt-v2v-input-xen(1)
        if enable_legacy_cp:
            process.run(
                'update-crypto-policies --set LEGACY',
                verbose=True,
                ignore_status=True,
                shell=True)

        v2v_params = {
            'hostname': remote_host, 'hypervisor': 'esx', 'main_vm': vm_name,
            'vpx_dc': vpx_dc, 'esx_ip': esx_ip,
            'new_name': vm_name + utils_misc.generate_random_string(4),
            'v2v_opts': v2v_opts, 'input_mode': 'libvirt',
            'os_storage': os_storage,
            'os_pool': os_pool,
            'network': params.get('network'),
            'bridge': params.get('bridge'),
            'target': params.get('target'),
            'password': vpx_passwd if src_uri_type != 'esx' else esxi_password,
            'input_transport': input_transport,
            'vcenter_host': vpx_hostname,
            'vcenter_password': vpx_passwd,
            'vddk_thumbprint': vddk_thumbprint,
            'vddk_libdir': vddk_libdir,
            'vddk_libdir_src': vddk_libdir_src,
            'src_uri_type': src_uri_type,
            'esxi_password': esxi_password,
            'esxi_host': esxi_host,
            'output_method': output_method,
            'os_storage_name': storage_name,
            'rhv_upload_opts': rhv_upload_opts,
            'oo_json_disk_pattern': json_disk_pattern,
            'cmd_has_ip': cmd_has_ip,
            'params': params
        }

        os.environ['LIBGUESTFS_BACKEND'] = 'direct'
        v2v_uri = utils_v2v.Uri('esx')
        remote_uri = v2v_uri.get_uri(remote_host, vpx_dc, esx_ip)

        # Create password file for access to ESX hypervisor
        vpx_passwd_file = params.get("vpx_passwd_file")
        with open(vpx_passwd_file, 'w') as pwd_f:
            if src_uri_type == 'esx':
                pwd_f.write(esxi_password)
            else:
                pwd_f.write(vpx_passwd)
        v2v_params['v2v_opts'] += " -ip %s" % vpx_passwd_file

        if params.get('output_format'):
            v2v_params.update({'of_format': params['output_format']})
        # Rename guest with special name while converting to rhev
        if '#' in vm_name and output_mode == 'rhev':
            v2v_params['new_name'] = v2v_params['new_name'].replace('#', '_')

        # Create SASL user on the ovirt host
        if output_mode == 'rhev':
            # create different sasl_user name for different job
            params.update({'sasl_user': params.get("sasl_user") +
                           utils_misc.generate_random_string(3)})
            logging.info('sals user name is %s' % params.get("sasl_user"))

            user_pwd = "[['%s', '%s']]" % (params.get("sasl_user"),
                                           params.get("sasl_pwd"))
            v2v_sasl = utils_sasl.SASL(sasl_user_pwd=user_pwd)
            v2v_sasl.server_ip = params.get("remote_ip")
            v2v_sasl.server_user = params.get('remote_user')
            v2v_sasl.server_pwd = params.get('remote_pwd')
            v2v_sasl.setup(remote=True)
            logging.debug('A SASL session %s was created', v2v_sasl)
            if output_method == 'rhv_upload':
                # Create password file for '-o rhv_upload' to connect to ovirt
                with open(rhv_passwd_file, 'w') as f:
                    f.write(rhv_passwd)
                # Copy ca file from ovirt to local
                remote.scp_from_remote(ovirt_hostname, 22, 'root',
                                       ovirt_engine_passwd,
                                       ovirt_ca_file_path,
                                       local_ca_file_path)

        # Create libvirt dir pool
        if output_mode == 'libvirt':
            pvt.pre_pool(pool_name, pool_type, pool_target, '')

        if 'root' in checkpoint and 'ask' in checkpoint:
            v2v_params['v2v_opts'] += ' --root ask'
            v2v_params['custom_inputs'] = params.get('choice', '2')
        if 'root' in checkpoint and 'ask' not in checkpoint:
            root_option = params.get('root_option')
            v2v_params['v2v_opts'] += ' --root %s' % root_option
        if 'with_proxy' in checkpoint:
            http_proxy = params.get('esx_http_proxy')
            https_proxy = params.get('esx_https_proxy')
            logging.info('Set http_proxy=%s, https_proxy=%s',
                         http_proxy, https_proxy)
            os.environ['http_proxy'] = http_proxy
            os.environ['https_proxy'] = https_proxy

        if 'ogac' in checkpoint:
            os.environ['VIRTIO_WIN'] = virtio_win_path
            if not os.path.exists(os.getenv('VIRTIO_WIN')):
                test.fail('%s does not exist' % os.getenv('VIRTIO_WIN'))

            if os.path.isdir(os.getenv('VIRTIO_WIN')) and os_type == 'linux':
                export_path = os.getenv('VIRTIO_WIN')
                qemu_guest_agent_dir = os.path.join(export_path, qa_path)
                if not os.path.exists(qemu_guest_agent_dir) and os.access(
                        export_path, os.W_OK) and qa_url:
                    logging.debug(
                        'Not found qemu-guest-agent in virtio-win or rhv-guest-tools-iso,'
                        ' Try to prepare it manually. This is not a permanant step, once'
                        ' the official build includes it, this step should be removed.')
                    os.makedirs(qemu_guest_agent_dir)
                    rpm_name = os.path.basename(qa_url)
                    download.get_file(
                        qa_url, os.path.join(
                            qemu_guest_agent_dir, rpm_name))

        if 'virtio_iso_blk' in checkpoint:
            if not os.path.exists(virtio_win_path):
                test.fail('%s does not exist' % virtio_win_path)

            # Find a free loop device
            free_loop_dev = process.run(
                "losetup --find", shell=True).stdout_text.strip()
            # Setup a loop device
            cmd = 'losetup %s %s' % (free_loop_dev, virtio_win_path)
            process.run(cmd, shell=True)
            os.environ['VIRTIO_WIN'] = free_loop_dev

        if 'block_dev' in checkpoint:
            os_directory = params_get(params, 'os_directory')
            block_count = params_get(params, 'block_count')
            os_directory = tempfile.TemporaryDirectory(prefix='v2v_test_', dir=os_directory)
            diskimage = '%s/diskimage' % os_directory.name
            # Update 'os_directory' for '-os' option
            params['os_directory'] = os_directory.name

            # Create a 1G image
            cmd = 'dd if=/dev/zero of=%s bs=10M count=%s' % (diskimage, block_count)
            process.run(cmd, shell=True)
            # Build filesystem
            cmd = 'mkfs.ext4 %s' % diskimage
            process.run(cmd, shell=True)
            # Find a free loop device
            free_loop_dev = process.run(
                "losetup --find", shell=True).stdout_text.strip()
            # Setup the image as a block device
            cmd = 'losetup %s %s' % (free_loop_dev, diskimage)
            process.run(cmd, shell=True)
            # Create a soft link to the loop device
            blk_dev_link = '%s/mydisk1' % os_directory.name
            cmd = 'ln -s %s %s' % (free_loop_dev, blk_dev_link)
            process.run(cmd, shell=True)

        if 'invalid_pem' in checkpoint:
            # simply change the 2nd line to lowercase to get an invalid pem
            with open(local_ca_file_path, 'r+') as fd:
                for i in range(2):
                    pos = fd.tell()
                    res = fd.readline()
                fd.seek(pos)
                fd.write(res.lower())
                fd.flush()

        if 'without_default_net' in checkpoint:
            net_name = find_net('virbr0')
            if net_name:
                destroy_net(net_name)

        if 'bandwidth' in checkpoint:
            dynamic_speeds = params_get(params, 'dynamic_speeds')
            bandwidth_file = params_get(params, 'bandwidth_file')
            with open(bandwidth_file, 'w') as fd:
                fd.write(dynamic_speeds)

        if checkpoint[0].startswith('virtio_win'):
            cp = checkpoint[0]
            src_dir = params.get('virtio_win_dir')
            dest_dir = os.path.join(data_dir.get_tmp_dir(), 'virtio-win')
            iso_path = os.path.join(dest_dir, 'virtio-win.iso')
            if not os.path.exists(dest_dir):
                shutil.copytree(src_dir, dest_dir)
            virtio_win_env = params.get('virtio_win_env', 'VIRTIO_WIN')
            process.run('rpm -e virtio-win')
            if process.run(
                'rpm -q virtio-win',
                    ignore_status=True).exit_status == 0:
                test.error('not removed')
            if cp.endswith('unset'):
                logging.info('Unset env %s' % virtio_win_env)
                os.unsetenv(virtio_win_env)
            if cp.endswith('custom'):
                logging.info('Set env %s=%s' % (virtio_win_env, dest_dir))
                os.environ[virtio_win_env] = dest_dir
            if cp.endswith('iso_mount'):
                logging.info('Mount iso to /opt')
                process.run('mount %s /opt' % iso_path)
                os.environ[virtio_win_env] = '/opt'
            if cp.endswith('iso_file'):
                logging.info('Set env %s=%s' % (virtio_win_env, iso_path))
                os.environ[virtio_win_env] = iso_path

        if 'luks_dev_keys' in checkpoint:
            luks_password = params_get(params, 'luks_password', '')
            luks_keys = params_get(params, 'luks_keys', '')
            keys_options = ' ' .join(
                list(map(lambda i: '--key %s' % i if i else '', luks_keys.split(';'))))

            if 'invalid_pwd_file' not in checkpoint:
                is_file_key = r'--key \S+:file:(\S+)'
                for file_key in re.findall(is_file_key, keys_options):
                    with open(file_key, 'w') as fd:
                        fd.write(luks_password)
            v2v_params['v2v_opts'] += ' ' + keys_options

        if 'empty_cdrom' in checkpoint:
            virsh_dargs = {'uri': remote_uri, 'remote_ip': remote_host,
                           'remote_user': 'root', 'remote_pwd': vpx_passwd,
                           'auto_close': True,
                           'debug': True}
            remote_virsh = virsh.VirshPersistent(**virsh_dargs)
            v2v_result = remote_virsh.dumpxml(vm_name)
            remote_virsh.close_session()
        else:
            if 'exist_uuid' in checkpoint:
                auto_clean = False
            if checkpoint[0] in [
                'mismatched_uuid',
                'no_uuid',
                'invalid_source',
                    'system_rhv_pem']:
                cmd_only = True
                auto_clean = False
            v2v_result = utils_v2v.v2v_cmd(
                v2v_params, auto_clean, cmd_only, interaction_run)
        if 'new_name' in v2v_params:
            vm_name = params['main_vm'] = v2v_params['new_name']

        if 'system_rhv_pem' in checkpoint:
            if 'set' in checkpoint:
                global_pem_setup(local_ca_file_path)
            rhv_cafile = r'-oo rhv-cafile=\S+\s*'
            new_cmd = utils_v2v.cmd_remove_option(v2v_result, rhv_cafile)
            logging.debug('New v2v command:\n%s', new_cmd)
        if 'mismatched_uuid' in checkpoint:
            # append more uuid
            new_cmd = v2v_result + ' -oo rhv-disk-uuid=%s' % str(uuid.uuid4())
        if 'no_uuid' in checkpoint:
            rhv_disk_uuid = r'-oo rhv-disk-uuid=\S+\s*'
            new_cmd = utils_v2v.cmd_remove_option(v2v_result, rhv_disk_uuid)
            logging.debug('New v2v command:\n%s', new_cmd)
        if 'exist_uuid' in checkpoint:
            # Use to cleanup the VM because it will not be run in check_result
            vmchecker = VMChecker(test, params, env)
            params['vmchecker'] = vmchecker
            # Update name to avoid conflict
            new_vm_name = v2v_params['new_name'] + '_exist_uuid'
            new_cmd = v2v_result.command.replace(
                '-on %s' %
                vm_name,
                '-on %s' %
                new_vm_name)
            new_cmd += ' --no-copy'
            logging.debug('re-run v2v command:\n%s', new_cmd)
        if 'invalid_source' in checkpoint:
            if params.get('invalid_vpx_hostname'):
                new_cmd = v2v_result.replace(
                    vpx_hostname, params.get('invalid_vpx_hostname'))
            if params.get('invalid_esx_hostname'):
                new_cmd = v2v_result.replace(
                    esxi_host, params.get('invalid_esx_hostname'))

        if checkpoint[0] in [
            'mismatched_uuid',
            'no_uuid',
            'invalid_source',
            'exist_uuid',
                'system_rhv_pem']:
            v2v_result = utils_v2v.cmd_run(
                new_cmd, params.get('v2v_dirty_resources'))

        check_result(v2v_result, status_error)

    finally:
        if enable_legacy_cp:
            process.run(
                'update-crypto-policies --set DEFAULT',
                verbose=True,
                ignore_status=True,
                shell=True)
        if checkpoint[0].startswith('virtio_win'):
            utils_package.package_install(['virtio-win'])
        if 'virtio_win_iso_mount' in checkpoint:
            process.run('umount /opt', ignore_status=True)
        if 'ogac' in checkpoint and params.get('tmp_mount_point'):
            if os.path.exists(params.get('tmp_mount_point')):
                utils_misc.umount(
                    os.getenv('VIRTIO_WIN'),
                    params['tmp_mount_point'],
                    'iso9660')
            os.environ.pop('VIRTIO_WIN')
        if 'block_dev' in checkpoint and hasattr(os_directory, 'name'):
            process.run('losetup -d %s' % free_loop_dev, shell=True)
            os_directory.cleanup()
        if 'virtio_iso_blk' in checkpoint:
            process.run('losetup -d %s' % free_loop_dev, shell=True)
            os.environ.pop('VIRTIO_WIN')
        if 'system_rhv_pem' in checkpoint and 'set' in checkpoint:
            global_pem_cleanup()
        if 'without_default_net' in checkpoint:
            if net_name:
                start_net(net_name)
        if params.get('vmchecker'):
            params['vmchecker'].cleanup()
        if output_mode == 'rhev' and v2v_sasl:
            v2v_sasl.cleanup()
            logging.debug('SASL session %s is closing', v2v_sasl)
            v2v_sasl.close_session()
        if output_mode == 'libvirt':
            pvt.cleanup_pool(pool_name, pool_type, pool_target, '')
        if 'with_proxy' in checkpoint:
            logging.info('Unset http_proxy&https_proxy')
            os.environ.pop('http_proxy')
            os.environ.pop('https_proxy')
        if unprivileged_user:
            process.system("userdel -fr %s" % unprivileged_user)
        # Cleanup constant files
        utils_v2v.cleanup_constant_files(params)
