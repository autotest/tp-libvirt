import os
import logging
import re

from avocado.core import exceptions
from avocado.utils import process

from virttest import virsh
from virttest import utils_v2v
from virttest import utils_sasl
from virttest import data_dir
from virttest.utils_test import libvirt

from provider.v2v_vmcheck_helper import VMChecker


def run(test, params, env):
    """
    Convert specific esx guest
    """
    for v in params.itervalues():
        if "V2V_EXAMPLE" in v:
            raise exceptions.TestSkipError("Please set real value for %s" % v)
    if utils_v2v.V2V_EXEC is None:
        raise ValueError('Missing command: virt-v2v')
    remote_host = params.get('remote_host')
    esx_ip = params.get('esx_ip')
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

    def check_vmtools(vmcheck):
        """
        Check whether vmware tools packages have been removed
        """
        pkgs = vmcheck.session.cmd('rpm -qa').strip()
        removed_pkgs = params.get('removed_pkgs').strip().split(',')
        if not removed_pkgs:
            raise exceptions.TestError('Missing param "removed_pkgs"')
        for pkg in removed_pkgs:
            if pkg in pkgs:
                log_fail('Package "%s" not removed' % pkg)
        # Check /etc/modprobe.conf
        content = vmcheck.session.cmd('cat /etc/modprobe.conf').strip()
        logging.debug(content)
        if not re.search('alias\s+eth0\s+virtio_net', content):
            log_fail('Not found "alias eth0 virtio_net')

    def check_v2v_log(output, check=None):
        """
        Check if error/warning meets expectation
        """
        # Fail if NOT found error message
        expect_map = {
            'GPO_AV': [
                'virt-v2v: warning: this guest has Windows Group Policy Objects',
                'virt-v2v: warning: this guest has Anti-Virus \(AV\) software'
            ],
            'no_ovmf': [
                'virt-v2v: error: cannot find firmware for UEFI guests',
                'You probably need to install OVMF\, or AAVMF'
            ]
        }
        if check not in expect_map:
            logging.info('Skip checking v2v log')
        else:
            for msg in expect_map[check]:
                if not utils_v2v.check_log(output, [msg]):
                    log_fail('Not found log:"%s"' % msg)
            logging.info('Finish checking v2v log')

    def check_result(result, status_error):
        """
        Check virt-v2v command result
        """
        libvirt.check_exit_status(result, status_error)
        output = result.stdout + result.stderr
        if not status_error:
            if output_mode == 'rhev':
                if not utils_v2v.import_vm_to_ovirt(params, address_cache,
                                                    timeout=v2v_timeout):
                    raise exceptions.TestFail('Import VM failed')
            elif output_mode == 'libvirt':
                virsh.start(vm_name)
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
            if checkpoint == 'vmtools':
                check_vmtools(vmchecker.checker)
            if checkpoint == 'GPO_AV':
                msg_list = [
                    'virt-v2v: warning: this guest has Windows Group Policy Objects',
                    'virt-v2v: warning: this guest has Anti-Virus \(AV\) software'
                ]
                for msg in msg_list:
                    if not utils_v2v.check_log(output, [msg]):
                        log_fail('Not found error message:"%s"' % msg)
            # Merge 2 error lists
            error_list.extend(vmchecker.errors)
        check_v2v_log(output, checkpoint)
        if len(error_list):
            raise exceptions.TestFail('%d checkpoints failed: %s' %
                                      (len(error_list), error_list))

    try:
        v2v_params = {
            'hostname': remote_host, 'hypervisor': 'esx', 'main_vm': vm_name,
            'vpx_dc': vpx_dc, 'esx_ip': esx_ip,
            'v2v_opts': '-v -x', 'input_mode': 'libvirt',
            'storage': params.get('output_storage', 'default'),
            'network': params.get('network'),
            'bridge':  params.get('bridge'),
            'target':  params.get('target')
        }

        os.environ['LIBGUESTFS_BACKEND'] = 'direct'

        # Create password file for access to ESX hypervisor
        vpx_passwd = params.get("vpx_password")
        logging.debug(vpx_passwd)
        vpx_passwd_file = os.path.join(data_dir.get_tmp_dir(), "vpx_passwd")
        with open(vpx_passwd_file, 'w') as pwd_f:
            pwd_f.write(vpx_passwd)
        v2v_params['v2v_opts'] += " --password-file %s" % vpx_passwd_file

        if params.get('output_format'):
            v2v_params.update({'output_format': params['output_format']})
        if params.get('new_name'):
            v2v_params.update({'new_name': params['new_name']})
        # Rename guest with special name while converting to rhev
        if '#' in vm_name and output_mode == 'rhev':
            v2v_params.update({'new_name': vm_name.replace('#', '_')})

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
            url = params.get('ovmf_url')
            if url and url.endswith('.rpm'):
                process.run('rpm -iv %s' % url)

        v2v_result = utils_v2v.v2v_cmd(v2v_params)
        if v2v_params.has_key('new_name'):
            params['main_vm'] = v2v_params['new_name']
        check_result(v2v_result, status_error)

    finally:
        if params.get('vmchecker'):
            params['vmchecker'].cleanup()
        if output_mode == 'libvirt':
            pvt.cleanup_pool(pool_name, pool_type, pool_target, '')
        if checkpoint == 'ovmf':
            process.run('rpm -q OVMF&&rpm -e OVMF', shell=True)
