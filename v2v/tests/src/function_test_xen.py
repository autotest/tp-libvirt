import os
import logging

from avocado.utils import process
from avocado.core import exceptions

from virttest import virsh
from virttest import utils_v2v
from virttest import utils_misc
from virttest import utils_sasl
from virttest import ssh_key
from virttest import remote
from virttest import data_dir
from virttest.utils_test import libvirt

from provider.v2v_vmcheck_helper import VMChecker


def run(test, params, env):
    """
    Convert specific xen guest
    """
    for v in params.itervalues():
        if "V2V_EXAMPLE" in v:
            raise exceptions.TestSkipError("Please set real value for %s" % v)
    if utils_v2v.V2V_EXEC is None:
        raise ValueError('Missing command: virt-v2v')
    vm_name = params.get('main_vm')
    new_vm_name = params.get('new_name')
    xen_host = params.get('xen_hostname')
    output_mode = params.get('output_mode')
    v2v_timeout = int(params.get('v2v_timeout', 1200))
    status_error = 'yes' == params.get('status_error', 'no')
    pool_name = params.get('pool_name', 'v2v_test')
    pool_type = params.get('pool_type', 'dir')
    pool_target = params.get('pool_target_path', 'v2v_pool')
    pvt = libvirt.PoolVolumeTest(test, params)
    address_cache = env.get('address_cache')
    checkpoint = params.get('checkpoint')

    def check_rhev_file_exist():
        """
        Check if rhev files exist
        """
        vmcheck = utils_v2v.VMCheck(test, params, env)
        vmcheck.boot_windows()
        vmcheck.create_session()
        file_path = {
            'rhev-apt.exe': r'C:\rhev-apt.exe',
            'rhsrvany.exe': r'"C:\program files\redhat\rhev\apt\rhsrvany.exe"'
        }
        fail = False
        for key in file_path:
            status = vmcheck.session.cmd_status('dir %s' % file_path[key])
            if not status:
                logging.error('%s exists' % key)
                fail = True
        vmcheck.session.close()
        if fail:
            raise exceptions.TestFail('RHEV file exists after convert to kvm')

    def check_grub_file(check):
        """
        Check grub file content
        """
        logging.info('Checking grub file')
        vmcheck = utils_v2v.VMCheck(test, params, env)
        vmcheck.create_session()
        grub_file = vmcheck.get_grub_path()
        try:
            if not grub_file:
                raise exceptions.TestError('Not found grub file')
            content = vmcheck.session.cmd('cat %s' % grub_file)
            if check == 'console_xvc0':
                if 'console=xvc0' in content:
                    raise exceptions.TestFail('"console=xvc0" still exists')
        finally:
            vmcheck.session.close()

    def check_v2v_log(output, check=None):
        """
        Check if error/warning meets expectation
        """
        # Fail if found error msg in log
        error_map = {
            'xvda_disk': [
                r'virt-v2v: WARNING: /boot/grub.*?/device.map references '
                r'unknown device /dev/vd.*?\n',
                r'virt-v2v: warning: /files/boot/grub/device.map/hd0 '
                r'references unknown.*?after conversion.'
            ],
            'xvda_guest': [
                r'virt-v2v: WARNING: /boot/grub.*?/device.map references '
                r'unknown device /dev/vd.*?\n',
                r'virt-v2v: warning: /files/boot/grub/device.map/hd0 '
                r'references unknown.*?after conversion.'
            ]
        }
        if check is None or check not in error_map:
            logging.info('Skip checking v2v log')
        else:
            if not utils_v2v.check_log(check, error_map[check], expect=False):
                raise exceptions.TestFail('Check v2v log Failed')
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
                try:
                    virsh.start(vm_name, debug=True, ignore_status=False)
                except Exception, e:
                    raise exceptions.TestFail('Start vm failed: %s', str(e))
            if checkpoint:
                if checkpoint == 'rhev_file':
                    check_rhev_file_exist()
                elif checkpoint == 'console_xvc0':
                    check_grub_file('console_xvc0')
                check_v2v_log(output, checkpoint)

    try:
        v2v_params = {
            'hostname': xen_host, 'hypervisor': 'xen', 'main_vm': vm_name,
            'v2v_opts': '-v -x', 'input_mode': 'libvirt',
            'new_name': new_vm_name,
            'storage':  params.get('output_storage', 'default'),
            'network':  params.get('network'),
            'bridge':   params.get('bridge'),
            'target':   params.get('target')
        }

        os.environ['LIBGUESTFS_BACKEND'] = 'direct'

        # Setup ssh-agent access to xen hypervisor
        xen_host_user = params.get('xen_host_user', 'root')
        xen_host_passwd = params.get('xen_host_passwd', 'redhat')
        logging.info('set up ssh-agent access ')
        ssh_key.setup_ssh_key(xen_host, user=xen_host_user,
                              port=22, password=xen_host_passwd)
        utils_misc.add_identities_into_ssh_agent()

        if params.get('output_format'):
            v2v_params.update({'output_format': params.get('output_format')})

        # Build rhev related options
        if output_mode == 'rhev':
            # Create SASL user on the ovirt host
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

        if checkpoint:
            uri = utils_v2v.Uri('xen').get_uri(xen_host)
            if checkpoint == 'guest_uuid':
                uuid = virsh.domuuid(vm_name, uri=uri).stdout.strip()
                v2v_params['main_vm'] = uuid
            elif checkpoint == 'xvda_disk':
                v2v_params['input_mode'] = 'disk'
                # Get remote disk image path
                blklist = virsh.domblklist(vm_name, uri=uri).stdout.split('\n')
                for line in blklist:
                    if line.startswith(('hda', 'vda', 'sda')):
                        remote_disk_image = line.split()[-1]
                        break
                # Local path of disk image
                input_file = data_dir.get_tmp_dir() + '/%s.img' % vm_name
                v2v_params.update({'input_file': input_file})
                # Copy remote image to local with scp
                remote.scp_from_remote(xen_host, 22, xen_host_user,
                                       xen_host_passwd, remote_disk_image,
                                       input_file)
            elif checkpoint == 'pool_uuid':
                virsh.pool_start(pool_name)
                pooluuid = virsh.pool_uuid(pool_name).stdout.strip()
                v2v_params['storage'] = pooluuid

        v2v_result = utils_v2v.v2v_cmd(v2v_params)

        if new_vm_name:
            vm_name = new_vm_name
            params['main_vm'] = new_vm_name

        check_result(v2v_result, status_error)

        # Check guest following the checkpoint document after convertion
        if not status_error:
            vmchecker = VMChecker(test, params, env)
            ret = vmchecker.run()
            if ret == 0:
                logging.info("All checkpoints passed")
            else:
                raise exceptions.TestFail("%s checkpoints failed" % ret)
    finally:
        process.run('ssh-agent -k')
        if output_mode in ['libvirt', 'rhev']:
            vmcheck = utils_v2v.VMCheck(test, params, env)
            vmcheck.cleanup()
        if output_mode == 'libvirt':
            pvt.cleanup_pool(pool_name, pool_type, pool_target, '')
