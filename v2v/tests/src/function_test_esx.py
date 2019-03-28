import os
import logging
import re

from avocado.utils import process

from virttest import data_dir
from virttest import utils_misc
from virttest import utils_package
from virttest import utils_sasl
from virttest import utils_v2v
from virttest import virsh
from virttest.utils_test import libvirt

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
    vpx_hostname = params.get('vpx_hostname')
    esx_ip = params.get('esx_hostname')
    vpx_dc = params.get('vpx_dc')
    vm_name = params.get('main_vm')
    output_mode = params.get('output_mode')
    pool_name = params.get('pool_name', 'v2v_test')
    pool_type = params.get('pool_type', 'dir')
    pool_target = params.get('pool_target_path', 'v2v_pool')
    pvt = libvirt.PoolVolumeTest(test, params)
    v2v_timeout = int(params.get('v2v_timeout', 1200))
    status_error = 'yes' == params.get('status_error', 'no')
    address_cache = env.get('address_cache')
    checkpoint = params.get('checkpoint', '')
    error_list = []
    remote_host = vpx_hostname

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
        # Only for grub2
        chkfiles = ['/etc/default/grub',
                    '/boot/grub2/grub.cfg',
                    '/etc/grub2.cfg']

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

    def check_result(result, status_error):
        """
        Check virt-v2v command result
        """
        libvirt.check_exit_status(result, status_error)
        output = result.stdout + result.stderr
        if checkpoint == 'empty_cdrom':
            if status_error:
                log_fail('Virsh dumpxml failed for empty cdrom image')
        elif not status_error:
            if output_mode == 'rhev':
                if not utils_v2v.import_vm_to_ovirt(params, address_cache,
                                                    timeout=v2v_timeout):
                    test.fail('Import VM failed')
            elif output_mode == 'libvirt':
                virsh.start(vm_name, debug=True)
            # Check guest following the checkpoint document after convertion
            logging.info('Checking common checkpoints for v2v')
            vmchecker = VMChecker(test, params, env)
            params['vmchecker'] = vmchecker
            if checkpoint not in ['GPO_AV', 'ovmf']:
                ret = vmchecker.run()
                if len(ret) == 0:
                    logging.info("All common checkpoints passed")
            # Check specific checkpoints
            if checkpoint == 'cdrom':
                virsh_session = utils_sasl.VirshSessionSASL(params)
                virsh_session_id = virsh_session.get_id()
                check_device_exist('cdrom', virsh_session_id)
            if checkpoint.startswith('vmtools'):
                check_vmtools(vmchecker.checker, checkpoint)
            if checkpoint == 'modprobe':
                check_modprobe(vmchecker.checker)
            if checkpoint == 'device_map':
                check_device_map(vmchecker.checker)
            if checkpoint == 'resume_swap':
                check_resume_swap(vmchecker.checker)
            # Merge 2 error lists
            error_list.extend(vmchecker.errors)
        log_check = utils_v2v.check_log(params, output)
        if log_check:
            log_fail(log_check)
        if len(error_list):
            test.fail('%d checkpoints failed: %s' %
                      (len(error_list), error_list))

    try:
        v2v_params = {
            'hostname': remote_host, 'hypervisor': 'esx', 'main_vm': vm_name,
            'vpx_dc': vpx_dc, 'esx_ip': esx_ip,
            'new_name': vm_name + utils_misc.generate_random_string(4),
            'v2v_opts': '-v -x', 'input_mode': 'libvirt',
            'storage': params.get('output_storage', 'default'),
            'network': params.get('network'),
            'bridge': params.get('bridge'),
            'target': params.get('target')
        }

        os.environ['LIBGUESTFS_BACKEND'] = 'direct'
        v2v_uri = utils_v2v.Uri('esx')
        remote_uri = v2v_uri.get_uri(remote_host, vpx_dc, esx_ip)

        # Create password file for access to ESX hypervisor
        vpx_passwd = params.get("vpx_password")
        logging.debug("vpx password is %s" % vpx_passwd)
        vpx_passwd_file = params.get("vpx_passwd_file")
        with open(vpx_passwd_file, 'w') as pwd_f:
            pwd_f.write(vpx_passwd)
        v2v_params['v2v_opts'] += " --password-file %s" % vpx_passwd_file

        if params.get('output_format'):
            v2v_params.update({'output_format': params['output_format']})
        # Rename guest with special name while converting to rhev
        if '#' in vm_name and output_mode == 'rhev':
            v2v_params['new_name'] = v2v_params['new_name'].replace('#', '_')

        # Create SASL user on the ovirt host
        if output_mode == 'rhev':
            user_pwd = "[['%s', '%s']]" % (params.get("sasl_user"),
                                           params.get("sasl_pwd"))
            v2v_sasl = utils_sasl.SASL(sasl_user_pwd=user_pwd)
            v2v_sasl.server_ip = params.get("remote_ip")
            v2v_sasl.server_user = params.get('remote_user')
            v2v_sasl.server_pwd = params.get('remote_pwd')
            v2v_sasl.setup(remote=True)

        # Create libvirt dir pool
        if output_mode == 'libvirt':
            pvt.pre_pool(pool_name, pool_type, pool_target, '')

        if checkpoint == 'ovmf':
            utils_package.package_install('OVMF')
        if checkpoint == 'root_ask':
            v2v_params['v2v_opts'] += ' --root ask'
            v2v_params['custom_inputs'] = params.get('choice', '2')
        if checkpoint.startswith('root_') and checkpoint != 'root_ask':
            root_option = params.get('root_option')
            v2v_params['v2v_opts'] += ' --root %s' % root_option
        if checkpoint == 'copy_to_local':
            esx_password = params.get('esx_password')
            esx_passwd_file = os.path.join(
                data_dir.get_tmp_dir(), "esx_passwd")
            logging.info('Prepare esx password file')
            with open(esx_passwd_file, 'w') as pwd_f:
                pwd_f.write(esx_password)
            esx_uri = 'esx://root@%s/?no_verify=1' % esx_ip
            copy_cmd = 'virt-v2v-copy-to-local -ic %s %s --password-file %s' %\
                       (esx_uri, vm_name, esx_passwd_file)
            process.run(copy_cmd)
            v2v_params['input_mode'] = 'libvirtxml'
            v2v_params['input_file'] = '%s.xml' % vm_name
        if checkpoint == 'with_proxy':
            http_proxy = params.get('esx_http_proxy')
            https_proxy = params.get('esx_https_proxy')
            logging.info('Set http_proxy=%s, https_proxy=%s',
                         http_proxy, https_proxy)
            os.environ['http_proxy'] = http_proxy
            os.environ['https_proxy'] = https_proxy

        if checkpoint == 'empty_cdrom':
            virsh_dargs = {'uri': remote_uri, 'remote_ip': remote_host,
                           'remote_user': 'root', 'remote_pwd': vpx_passwd,
                           'debug': True}
            remote_virsh = virsh.VirshPersistent(**virsh_dargs)
            v2v_result = remote_virsh.dumpxml(vm_name)
        else:
            v2v_result = utils_v2v.v2v_cmd(v2v_params)
        if 'new_name' in v2v_params:
            vm_name = params['main_vm'] = v2v_params['new_name']
        check_result(v2v_result, status_error)

    finally:
        if params.get('vmchecker'):
            params['vmchecker'].cleanup()
        if output_mode == 'libvirt':
            pvt.cleanup_pool(pool_name, pool_type, pool_target, '')
        if checkpoint == 'with_proxy':
            logging.info('Unset http_proxy&https_proxy')
            os.environ.pop('http_proxy')
            os.environ.pop('https_proxy')
        # Cleanup constant files
        utils_v2v.cleanup_constant_files(params)
