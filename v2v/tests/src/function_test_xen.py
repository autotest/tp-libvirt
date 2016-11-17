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
from virttest.libvirt_xml import vm_xml
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
    new_vm_name = params.get('new_vm_name')
    xen_host = params.get('xen_hostname')
    xen_host_user = params.get('xen_host_user', 'root')
    xen_host_passwd = params.get('xen_host_passwd', 'redhat')
    output_mode = params.get('output_mode')
    v2v_timeout = int(params.get('v2v_timeout', 1200))
    status_error = 'yes' == params.get('status_error', 'no')
    pool_name = params.get('pool_name', 'v2v_test')
    pool_type = params.get('pool_type', 'dir')
    pool_target = params.get('pool_target_path', 'v2v_pool')
    pvt = libvirt.PoolVolumeTest(test, params)
    address_cache = env.get('address_cache')
    checkpoint = params.get('checkpoint', '')
    bk_list = ['vnc_autoport', 'vnc_encrypt']
    error_list = []

    def log_fail(msg):
        """
        Log error and update error list
        """
        logging.error(msg)
        error_list.append(msg)

    def set_graphics(virsh_instance, param):
        """
        Set graphics attributes of vm xml
        """
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name, virsh_instance=virsh_instance)
        graphic = vmxml.xmltreefile.find('devices').find('graphics')
        for key in param:
            logging.debug('Set %s=\'%s\'' % (key, param[key]))
            graphic.set(key, param[key])
        vmxml.sync(virsh_instance=virsh_instance)

    def check_rhev_file_exist(vmcheck):
        """
        Check if rhev files exist
        """
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
        if fail:
            log_fail('RHEV file exists after convert to kvm')

    def check_grub_file(vmcheck, check):
        """
        Check grub file content
        """
        logging.info('Checking grub file')
        grub_file = utils_misc.get_bootloader_cfg(session=vmcheck.session)
        if not grub_file:
            raise exceptions.TestError('Not found grub file')
        content = vmcheck.session.cmd('cat %s' % grub_file)
        if check == 'console_xvc0':
            if 'console=xvc0' in content:
                log_fail('"console=xvc0" still exists')

    def check_kernel(vmcheck):
        """
        Check content of /etc/sysconfig/kernel
        """
        logging.info('Checking /etc/sysconfig/kernel file')
        content = vmcheck.session.cmd('cat /etc/sysconfig/kernel')
        logging.debug(content)
        if 'DEFAULTKERNEL=kernel' not in content:
            log_fail('Not find "DEFAULTKERNEL=kernel"')
        elif 'DEFAULTKERNEL=kernel-xen' in content:
            log_fail('DEFAULTKERNEL is "kernel-xen"')

    def check_sound_card(vmcheck, check):
        """
        Check sound status of vm from xml
        """
        xml = virsh.dumpxml(vm_name, session_id=vmcheck.virsh_session_id).stdout
        logging.debug(xml)
        if check == 'sound' and '<sound model' in xml:
            log_fail('Sound card should be removed')
        if check == 'pcspk' and "<sound model='pcspk'" not in xml:
            log_fail('Sound card should be "pcspk"')

    def check_v2v_log(output, check=None):
        """
        Check if error/warning meets expectation
        """
        # Fail if found error msg in log
        not_expect_map = {
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
            ],
            'libguestfs_backend_empty': ['libguestfs: error: invalid backend:']
        }
        expect_map = {
            'same_name': ["virt-v2v: error: a libvirt domain called '.*?' "
                          "already exists on the target."],
            'libguestfs_backend_test': ['export LIBGUESTFS_BACKEND=direct',
                                        'libguestfs: error: invalid backend: .*?'],
            'no_passwordless_SSH': [
                'virt-v2v: error: ssh-agent authentication has not been set up',
                '\$SSH_AUTH_SOCK is not set',
                'Please read "INPUT FROM RHEL 5 XEN" in the'
            ],
            'xml_without_image': ["Could not open '.*?': No such file or directory"],
            'pv_no_regular_kernel':
                ['virt-v2v: error: only Xen kernels are installed in this guest']
        }

        if check is None or not (check in not_expect_map or check in expect_map):
            logging.info('Skip checking v2v log')
        else:
            logging.info('Checking v2v log')
            if expect_map.has_key(check):
                expect = True
                content_map = expect_map
            elif not_expect_map.has_key(check):
                expect = False
                content_map = not_expect_map
            if utils_v2v.check_log(output, content_map[check], expect=expect):
                logging.info('Finish checking v2v log')
            else:
                raise exceptions.TestFail('Check v2v log failed')

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
            # Check guest following the checkpoint document after convertion
            logging.info('Checking common checkpoints for v2v')
            vmchecker = VMChecker(test, params, env)
            params['vmchecker'] = vmchecker
            ret = vmchecker.run()
            if len(ret) == 0:
                logging.info("All common checkpoints passed")
            # Check specific checkpoints
            if checkpoint == 'rhev_file':
                check_rhev_file_exist(vmchecker.checker)
            elif checkpoint == 'console_xvc0':
                check_grub_file(vmchecker.checker, 'console_xvc0')
            elif checkpoint in ('vnc_autoport', 'vnc_encrypt'):
                vmchecker.check_graphics(params[checkpoint])
            elif checkpoint == 'sdl':
                if output_mode == 'libvirt':
                    vmchecker.check_graphics({'type': 'vnc'})
                elif output_mode == 'rhev':
                    vmchecker.check_graphics({'type': 'spice'})
            elif checkpoint == 'pv_with_regular_kernel':
                check_kernel(vmchecker.checker)
            elif checkpoint in ['sound', 'pcspk']:
                check_sound_card(vmchecker.checker, checkpoint)
        check_v2v_log(output, checkpoint)
        # Merge 2 error lists
        if params.get('vmchecker'):
            error_list.extend(params['vmchecker'].errors)
        if len(error_list):
            raise exceptions.TestFail('%d checkpoints failed: %s' %
                                      (len(error_list), error_list))

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

        bk_xml = None
        os.environ['LIBGUESTFS_BACKEND'] = 'direct'

        # Setup ssh-agent access to xen hypervisor
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

        uri = utils_v2v.Uri('xen').get_uri(xen_host)

        # Check if xen guest exists
        if not virsh.domain_exists(vm_name, uri=uri):
            logging.error('VM %s not exists', vm_name)

        if checkpoint in bk_list:
            virsh_instance = virsh.VirshPersistent()
            virsh_instance.set_uri(uri)
            bk_xml = vm_xml.VMXML.new_from_inactive_dumpxml(
                    vm_name, virsh_instance=virsh_instance)
        if checkpoint == 'guest_uuid':
            uuid = virsh.domuuid(vm_name, uri=uri).stdout.strip()
            v2v_params['main_vm'] = uuid
        if checkpoint in ['format_convert', 'xvda_disk']:
            # Get remote disk image path
            blklist = virsh.domblklist(vm_name, uri=uri).stdout.split('\n')
            logging.debug('domblklist %s:\n%s', vm_name, blklist)
            for line in blklist:
                if line.startswith(('hda', 'vda', 'sda')):
                    params['remote_disk_image'] = line.split()[-1]
                    break
            # Local path of disk image
            params['img_path'] = data_dir.get_tmp_dir() + '/%s.img' % vm_name
            if checkpoint == 'xvda_disk':
                v2v_params['input_mode'] = 'disk'
                v2v_params.update({'input_file': params['img_path']})
            # Copy remote image to local with scp
            remote.scp_from_remote(xen_host, 22, xen_host_user,
                                   xen_host_passwd,
                                   params['remote_disk_image'],
                                   params['img_path'])
        if checkpoint == 'pool_uuid':
            virsh.pool_start(pool_name)
            pooluuid = virsh.pool_uuid(pool_name).stdout.strip()
            v2v_params['storage'] = pooluuid
        if checkpoint.startswith('vnc'):
            vm_xml.VMXML.set_graphics_attr(vm_name, {'type': 'vnc'},
                                           virsh_instance=virsh_instance)
            if checkpoint == 'vnc_autoport':
                params[checkpoint] = {'autoport': 'yes'}
                vm_xml.VMXML.set_graphics_attr(vm_name, params[checkpoint],
                                               virsh_instance=virsh_instance)
            elif checkpoint == 'vnc_encrypt':
                params[checkpoint] = {'passwd': params.get('vnc_passwd', 'redhat')}
                vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(
                        vm_name, virsh_instance=virsh_instance)
                vm_xml.VMXML.add_security_info(
                        vmxml, params[checkpoint]['passwd'],
                        virsh_instance=virsh_instance)
            logging.debug(virsh_instance.dumpxml(vm_name, extra='--security-info'))
        if checkpoint.startswith('libguestfs_backend'):
            value = checkpoint[19:]
            if value == 'empty':
                value = ''
            logging.info('Set LIBGUESTFS_BACKEND to "%s"', value)
            os.environ['LIBGUESTFS_BACKEND'] = value
        if checkpoint == 'same_name':
            logging.info('Convert guest and rename to %s', new_vm_name)
            v2v_params.update({'new_name': new_vm_name})
        if checkpoint == 'no_passwordless_SSH':
            logging.info('Unset $SSH_AUTH_SOCK')
            os.unsetenv('SSH_AUTH_SOCK')
        if checkpoint in ['xml_without_image', 'format_convert']:
            xml_file = os.path.join(data_dir.get_tmp_dir(), '%s.xml' % vm_name)
            virsh.dumpxml(vm_name, to_file=xml_file, uri=uri)
            v2v_params['hypervisor'] = 'kvm'
            v2v_params['input_mode'] = 'libvirtxml'
            v2v_params.update({'input_file': xml_file})
            if params.get('img_path'):
                cmd = "sed -i 's|%s|%s|' %s" % (params['remote_disk_image'],
                                                params['img_path'], xml_file)
                process.run(cmd)
                logging.debug(process.run('cat %s' % xml_file).stdout)
            if checkpoint == 'format_convert':
                v2v_params['output_format'] = 'qcow2'
        if checkpoint == 'ssh_banner':
            session = remote.remote_login("ssh", xen_host, "22", "root",
                                          xen_host_passwd, "#")
            ssh_banner_content = r'"# no default banner path\n' \
                                 r'#Banner /path/banner file\n' \
                                 r'Banner /etc/ssh/ssh_banner"'
            logging.info('Create ssh_banner file')
            session.cmd('echo -e %s > /etc/ssh/ssh_banner' % ssh_banner_content)
            logging.info('Content of ssh_banner file:')
            logging.info(session.cmd_output('cat /etc/ssh/ssh_banner'))
            logging.info('Restart sshd service on xen host')
            session.cmd('service sshd restart')

        # Check if xen guest exists again
        if not virsh.domain_exists(vm_name, uri=uri):
            logging.error('VM %s not exists', vm_name)

        # Execute virt-v2v
        v2v_result = utils_v2v.v2v_cmd(v2v_params)

        if new_vm_name:
            vm_name = new_vm_name
            params['main_vm'] = new_vm_name
        check_result(v2v_result, status_error)
    finally:
        process.run('ssh-agent -k')
        if params.get('vmchecker'):
            params['vmchecker'].cleanup()
        if output_mode == 'libvirt':
            pvt.cleanup_pool(pool_name, pool_type, pool_target, '')
        if bk_xml:
            bk_xml.sync(virsh_instance=virsh_instance)
            virsh_instance.close_session()
        if checkpoint == 'ssh_banner':
            logging.info('Remove ssh_banner file')
            session = remote.remote_login("ssh", xen_host, "22", "root",
                                          xen_host_passwd, "#")
            session.cmd('rm -f /etc/ssh/ssh_banner')
            session.cmd('service sshd restart')
