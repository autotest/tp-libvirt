import os
import logging
import re
import uuid
import shutil

from virttest import utils_misc
from virttest import utils_sasl
from virttest import utils_v2v
from virttest import virsh
from virttest import remote
from virttest.utils_test import libvirt
from avocado.utils import process
from avocado.utils import download
from aexpect.exceptions import ShellProcessTerminatedError

from provider.v2v_vmcheck_helper import VMChecker


def run(test, params, env):
    """
    Convert specific esx guest
    """
    for v in list(params.values()):
        if "V2V_EXAMPLE" in v:
            test.cancel("Please set real value for %s" % v)
    if utils_v2v.V2V_EXEC is None:
        raise ValueError('Missing command: virt-v2v')
    version_requried = params.get("version_requried")
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
    v2v_opts = '-v -x' if params.get('v2v_debug', 'on') == 'on' else ''
    if params.get("v2v_opts"):
        # Add a blank by force
        v2v_opts += ' ' + params.get("v2v_opts")
    status_error = 'yes' == params.get('status_error', 'no')
    address_cache = env.get('address_cache')
    checkpoint = params.get('checkpoint', '')
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
    # For construct rhv-upload option in v2v cmd
    output_method = params.get("output_method")
    rhv_upload_opts = params.get("rhv_upload_opts")
    storage_name = params.get('storage_name')
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
        if check == 'vmtools':
            logging.info('Check if packages been removed')
            pkgs = vmcheck.session.cmd('rpm -qa').strip()
            removed_pkgs = params.get('removed_pkgs').strip().split(',')
            if not removed_pkgs:
                test.error('Missing param "removed_pkgs"')
            for pkg in removed_pkgs:
                if pkg in pkgs:
                    log_fail('Package "%s" not removed' % pkg)
        elif check == 'vmtools_service':
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

    def check_windows_ogac(vmcheck):
        """
        Check qemu-guest-agent service in VM

        :param vmcheck: VMCheck object for vm checking
        """
        try:
            res = utils_misc.wait_for(
                lambda: re.search(
                    'running',
                    vmcheck.get_service_info('qemu-ga'),
                    re.I),
                300,
                step=30)
        except ShellProcessTerminatedError:
            # Windows guest may reboot after installing qemu-ga service
            logging.debug('Windows guest is rebooting')
            if vmcheck.session:
                vmcheck.session.close()
                vmcheck.session = None
            vmcheck.create_session()
            res = utils_misc.wait_for(
                lambda: re.search(
                    'running',
                    vmcheck.get_service_info('qemu-ga'),
                    re.I),
                300,
                step=30)

        if not res:
            test.fail('Not found running qemu-ga service')

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

        # If qemu-guest-agent version in VM is higher than the pkg in qemu-guest-agent-iso,
        # v2v will not update the qemu-guest-agent version and report a warning.
        #
        # e.g.
        # virt-v2v: warning: failed to install QEMU Guest Agent: command:         package
        # qemu-guest-agent-10:2.12.0-3.el7.x86_64 (which is newer than
        # qemu-guest-agent-10:2.12.0-2.el7.x86_64) is already installed
        if not any([vm_pkg_ver in pkg for pkg in all_pkgs]):
            logging.debug(
                'Wrong qemu-guest-agent version, maybe it is higher than package version in ISO')
            logging.info(
                'Unexpected qemu-guest-agent version, set v2v log checking')
            expect_msg_ptn = r'virt-v2v: warning: failed to install QEMU Guest Agent.*?is newer than.*? is already installed'
            params.update({'msg_content': expect_msg_ptn, 'expect_msg': 'yes'})

        # Check the service status of qemu-guest-agent in VM
        status_ptn = r'Active: active \(running\)|qemu-ga \(pid +[0-9]+\) is running'
        cmd = 'service qemu-ga status;systemctl status qemu-guest-agent'
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

    def cmd_remove_option(cmd, opt_pattern):
        """
        Remove an option from cmd

        :param cmd: the cmd
        :param opt_pattern: a pattern stands for the option
        """
        for item in re.findall(opt_pattern, cmd):
            cmd = cmd.replace(item, '').strip()
        return cmd

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

    def check_result(result, status_error):
        """
        Check virt-v2v command result
        """
        libvirt.check_exit_status(result, status_error)
        output = result.stdout_text + result.stderr_text
        if checkpoint == 'empty_cdrom':
            if status_error:
                log_fail('Virsh dumpxml failed for empty cdrom image')
        elif not status_error:
            vmchecker = VMChecker(test, params, env)
            params['vmchecker'] = vmchecker
            if output_mode == 'rhev':
                if not utils_v2v.import_vm_to_ovirt(params, address_cache,
                                                    timeout=v2v_timeout):
                    test.fail('Import VM failed')
            elif output_mode == 'libvirt':
                virsh.start(vm_name, debug=True)
            # Check guest following the checkpoint document after convertion
            logging.info('Checking common checkpoints for v2v')
            if skip_vm_check != 'yes':
                if checkpoint == 'ogac':
                    # windows guests will reboot at any time after qemu-ga is
                    # installed. The process cannot be controled. In order to
                    # don't break vmchecker.run() process, It's better to put
                    # check_windows_ogac before vmchecker.run(). Because in
                    # check_windows_ogac, it waits until rebooting completes.
                    vmchecker.checker.create_session()
                    if os_type == 'windows':
                        check_windows_ogac(vmchecker.checker)
                    else:
                        check_linux_ogac(vmchecker.checker)
                ret = vmchecker.run()
                if len(ret) == 0:
                    logging.info("All common checkpoints passed")
            else:
                logging.info(
                    'Skip checking vm after conversion: %s' %
                    skip_reason)
            # Check specific checkpoints
            if checkpoint == 'cdrom':
                virsh_session = utils_sasl.VirshSessionSASL(params)
                virsh_session_id = virsh_session.get_id()
                check_device_exist('cdrom', virsh_session_id)
                virsh_session.close()
            if checkpoint.startswith('vmtools'):
                check_vmtools(vmchecker.checker, checkpoint)
            if checkpoint == 'modprobe':
                check_modprobe(vmchecker.checker)
            if checkpoint == 'device_map':
                check_device_map(vmchecker.checker)
            if checkpoint == 'resume_swap':
                check_resume_swap(vmchecker.checker)
            if checkpoint == 'rhev_file':
                check_rhev_file_exist(vmchecker.checker)
            if checkpoint == 'file_architecture':
                check_file_architecture(vmchecker.checker)
            if checkpoint == 'ubuntu_tools':
                check_ubuntools(vmchecker.checker)
            if checkpoint == 'without_default_net':
                if virsh.net_state_dict()[net_name]['active']:
                    log_fail("Bridge virbr0 already started during conversion")
            # Merge 2 error lists
            error_list.extend(vmchecker.errors)
        log_check = utils_v2v.check_log(params, output)
        if log_check:
            log_fail(log_check)
        if len(error_list):
            test.fail('%d checkpoints failed: %s' %
                      (len(error_list), error_list))

    try:
        if version_requried and not utils_v2v.compare_version(
                version_requried):
            test.cancel("Testing requries version: %s" % version_requried)

        v2v_params = {
            'hostname': remote_host, 'hypervisor': 'esx', 'main_vm': vm_name,
            'vpx_dc': vpx_dc, 'esx_ip': esx_ip,
            'new_name': vm_name + utils_misc.generate_random_string(4),
            'v2v_opts': v2v_opts, 'input_mode': 'libvirt',
            'storage': params.get('output_storage', 'default'),
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
            'storage_name': storage_name,
            'rhv_upload_opts': rhv_upload_opts,
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
            v2v_params.update({'output_format': params['output_format']})
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

        if checkpoint == 'root_ask':
            v2v_params['v2v_opts'] += ' --root ask'
            v2v_params['custom_inputs'] = params.get('choice', '2')
        if checkpoint.startswith('root_') and checkpoint != 'root_ask':
            root_option = params.get('root_option')
            v2v_params['v2v_opts'] += ' --root %s' % root_option
        if checkpoint == 'with_proxy':
            http_proxy = params.get('esx_http_proxy')
            https_proxy = params.get('esx_https_proxy')
            logging.info('Set http_proxy=%s, https_proxy=%s',
                         http_proxy, https_proxy)
            os.environ['http_proxy'] = http_proxy
            os.environ['https_proxy'] = https_proxy

        if checkpoint == 'ogac':
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

        if checkpoint == 'virtio_iso_blk':
            if not os.path.exists(virtio_win_path):
                test.fail('%s does not exist' % virtio_win_path)

            # Find a free loop device
            free_loop_dev = process.run(
                "losetup --find", shell=True).stdout_text.strip()
            # Setup a loop device
            cmd = 'losetup %s %s' % (free_loop_dev, virtio_win_path)
            process.run(cmd, shell=True)
            os.environ['VIRTIO_WIN'] = free_loop_dev

        if checkpoint == 'invalid_pem':
            # simply change the 2nd line to lowercase to get an invalid pem
            with open(local_ca_file_path, 'r+') as fd:
                for i in range(2):
                    pos = fd.tell()
                    res = fd.readline()
                fd.seek(pos)
                fd.write(res.lower())
                fd.flush()

        if checkpoint == 'without_default_net':
            net_name = find_net('virbr0')
            if net_name:
                destroy_net(net_name)

        if checkpoint == 'empty_cdrom':
            virsh_dargs = {'uri': remote_uri, 'remote_ip': remote_host,
                           'remote_user': 'root', 'remote_pwd': vpx_passwd,
                           'auto_close': True,
                           'debug': True}
            remote_virsh = virsh.VirshPersistent(**virsh_dargs)
            v2v_result = remote_virsh.dumpxml(vm_name)
            remote_virsh.close_session()
        else:
            if checkpoint == 'exist_uuid':
                auto_clean = False
            if checkpoint in [
                'mismatched_uuid',
                'no_uuid',
                'system_rhv_pem_set',
                    'system_rhv_pem_unset']:
                cmd_only = True
                auto_clean = False
            v2v_result = utils_v2v.v2v_cmd(v2v_params, auto_clean, cmd_only)
        if 'new_name' in v2v_params:
            vm_name = params['main_vm'] = v2v_params['new_name']

        if checkpoint.startswith('system_rhv_pem'):
            if checkpoint == 'system_rhv_pem_set':
                global_pem_setup(local_ca_file_path)
            rhv_cafile = r'-oo rhv-cafile=\S+\s*'
            new_cmd = cmd_remove_option(v2v_result, rhv_cafile)
            logging.debug('New v2v command:\n%s', new_cmd)
        if checkpoint == 'mismatched_uuid':
            # append more uuid
            new_cmd = v2v_result + ' -oo rhv-disk-uuid=%s' % str(uuid.uuid4())
        if checkpoint == 'no_uuid':
            rhv_disk_uuid = r'-oo rhv-disk-uuid=\S+\s*'
            new_cmd = cmd_remove_option(v2v_result, rhv_disk_uuid)
            logging.debug('New v2v command:\n%s', new_cmd)
        if checkpoint == 'exist_uuid':
            new_vm_name = v2v_params['new_name'] + '_exist_uuid'
            new_cmd = v2v_result.command.replace(
                '-on %s' %
                vm_name,
                '-on %s' %
                new_vm_name)
            logging.debug('re-run v2v command:\n%s', new_cmd)

        if checkpoint in [
            'mismatched_uuid',
            'no_uuid',
            'exist_uuid',
            'system_rhv_pem_set',
                'system_rhv_pem_unset']:
            v2v_result = utils_v2v.cmd_run(
                new_cmd, params.get('v2v_dirty_resources'))

        check_result(v2v_result, status_error)

    finally:
        if checkpoint == 'ogac' and params.get('tmp_mount_point'):
            if os.path.exists(params.get('tmp_mount_point')):
                utils_misc.umount(
                    os.getenv('VIRTIO_WIN'),
                    params['tmp_mount_point'],
                    'iso9660')
            os.environ.pop('VIRTIO_WIN')
        if checkpoint == 'virtio_iso_blk':
            process.run('losetup -d %s' % free_loop_dev, shell=True)
            os.environ.pop('VIRTIO_WIN')
        if checkpoint == 'system_rhv_pem_set':
            global_pem_cleanup()
        if checkpoint == 'without_default_net':
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
        if checkpoint == 'with_proxy':
            logging.info('Unset http_proxy&https_proxy')
            os.environ.pop('http_proxy')
            os.environ.pop('https_proxy')
        # Cleanup constant files
        utils_v2v.cleanup_constant_files(params)
