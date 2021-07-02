import os
import logging
import shutil

from avocado.utils import process

from virttest import virsh
from virttest import utils_v2v
from virttest import utils_libvirtd
from virttest import utils_misc
from virttest import utils_package
from virttest import utils_sasl
from virttest import remote
from virttest import data_dir
from virttest.libvirt_xml import vm_xml
from virttest.staging import service
from virttest.utils_test import libvirt

from provider.v2v_vmcheck_helper import VMChecker


def run(test, params, env):
    """
    Convert specific xen guest
    """
    for v in list(params.values()):
        if "V2V_EXAMPLE" in v:
            test.cancel("Please set real value for %s" % v)
    if utils_v2v.V2V_EXEC is None:
        test.cancel('Missing command: virt-v2v')
    vm_name = params.get('main_vm')
    new_vm_name = params.get('new_vm_name')
    xen_host = params.get('xen_hostname')
    xen_host_user = params.get('xen_host_user', 'root')
    xen_host_passwd = params.get('xen_host_passwd', 'redhat')
    output_mode = params.get('output_mode')
    v2v_timeout = int(params.get('v2v_timeout', 1200))
    v2v_opts = '-v -x' if params.get('v2v_debug', 'on') == 'on' else ''
    if params.get("v2v_opts"):
        # Add a blank by force
        v2v_opts += ' ' + params.get("v2v_opts")
    status_error = 'yes' == params.get('status_error', 'no')
    skip_vm_check = params.get('skip_vm_check', 'no')
    skip_reason = params.get('skip_reason')
    pool_name = params.get('pool_name', 'v2v_test')
    pool_type = params.get('pool_type', 'dir')
    pool_target = params.get('pool_target_path', 'v2v_pool')
    pvt = libvirt.PoolVolumeTest(test, params)
    address_cache = env.get('address_cache')
    checkpoint = params.get('checkpoint', '')
    bk_list = ['vnc_autoport', 'vnc_encrypt', 'vnc_encrypt_warning']
    error_list = []
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
    virsh_instance = None

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
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(
            vm_name, virsh_instance=virsh_instance)
        graphic = vmxml.xmltreefile.find('devices').find('graphics')
        for key in param:
            logging.debug('Set %s=\'%s\'' % (key, param[key]))
            graphic.set(key, param[key])
        vmxml.sync(virsh_instance=virsh_instance)

    def check_grub_file(vmcheck, check):
        """
        Check grub file content
        """
        logging.info('Checking grub file')
        grub_file = utils_misc.get_bootloader_cfg(session=vmcheck.session)
        if not grub_file:
            test.error('Not found grub file')
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
        xml = virsh.dumpxml(
            vm_name, session_id=vmcheck.virsh_session_id).stdout
        logging.debug(xml)
        if check == 'sound' and '<sound model' in xml:
            log_fail('Sound card should be removed')
        if check == 'pcspk' and output_mode == 'libvirt' and "<sound model='pcspk'" not in xml:
            log_fail('Sound card should be "pcspk"')

    def check_disk(vmcheck, count):
        """
        Check if number of disks meets expectation
        """
        logging.info('Expect number of disks: %d', count)
        actual = vmcheck.session.cmd('lsblk |grep disk |wc -l').strip()
        logging.info('Actual number of disks: %s', actual)
        if int(actual) != count:
            log_fail('Number of disks is wrong')

    def check_result(result, status_error):
        """
        Check virt-v2v command result
        """
        libvirt.check_exit_status(result, status_error)
        output = result.stdout_text + result.stderr_text
        if not status_error and checkpoint != 'vdsm':
            vmchecker = VMChecker(test, params, env)
            params['vmchecker'] = vmchecker
            if output_mode == 'rhev':
                if not utils_v2v.import_vm_to_ovirt(params, address_cache,
                                                    timeout=v2v_timeout):
                    test.fail('Import VM failed')
            elif output_mode == 'libvirt':
                try:
                    virsh.start(vm_name, debug=True, ignore_status=False)
                except Exception as e:
                    test.fail('Start vm failed: %s' % str(e))
            # Check guest following the checkpoint document after convertion
            logging.info('Checking common checkpoints for v2v')
            if params.get('skip_vm_check') != 'yes':
                ret = vmchecker.run()
                if len(ret) == 0:
                    logging.info("All common checkpoints passed")
            else:
                logging.info(
                    'Skip checking vm after conversion: %s' %
                    skip_reason)
            # Check specific checkpoints
            if checkpoint == 'console_xvc0':
                check_grub_file(vmchecker.checker, 'console_xvc0')
            if checkpoint in ('vnc_autoport', 'vnc_encrypt'):
                vmchecker.check_graphics(params[checkpoint])
            if checkpoint == 'sdl':
                if output_mode == 'libvirt':
                    vmchecker.check_graphics({'type': 'vnc'})
                elif output_mode == 'rhev':
                    vmchecker.check_graphics({'type': 'spice'})
            if checkpoint == 'pv_with_regular_kernel':
                check_kernel(vmchecker.checker)
            if checkpoint in ['sound', 'pcspk']:
                check_sound_card(vmchecker.checker, checkpoint)
            if checkpoint == 'multidisk':
                check_disk(vmchecker.checker, params['disk_count'])
        log_check = utils_v2v.check_log(params, output)
        if log_check:
            log_fail(log_check)
        # Merge 2 error lists
        if params.get('vmchecker'):
            error_list.extend(params['vmchecker'].errors)
        if len(error_list):
            test.fail(
                '%d checkpoints failed: %s' %
                (len(error_list), error_list))

    try:
        v2v_sasl = None

        v2v_params = {
            'hostname': xen_host, 'hypervisor': 'xen', 'main_vm': vm_name,
            'v2v_opts': v2v_opts, 'input_mode': 'libvirt',
            'new_name': new_vm_name,
            'password': xen_host_passwd,
            'os_pool': os_pool,
            'os_storage': os_storage,
            'os_storage_name': storage_name,
            'network': params.get('network'),
            'bridge': params.get('bridge'),
            'target': params.get('target'),
            'output_method': output_method,
            'rhv_upload_opts': rhv_upload_opts,
            'params': params
        }

        bk_xml = None
        os.environ['LIBGUESTFS_BACKEND'] = 'direct'

        # See man virt-v2v-input-xen(1)
        process.run(
            'update-crypto-policies --set LEGACY',
            verbose=True,
            ignore_status=True,
            shell=True)

        # Setup ssh-agent access to xen hypervisor
        logging.info('set up ssh-agent access ')
        xen_pubkey, xen_session = utils_v2v.v2v_setup_ssh_key(
            xen_host, xen_host_user, xen_host_passwd, auto_close=False)
        utils_misc.add_identities_into_ssh_agent()

        if params.get('output_format'):
            v2v_params.update({'of_format': params.get('output_format')})

        # Build rhev related options
        if output_mode == 'rhev':
            # To RHV doesn't support 'qcow2' right now
            v2v_params['of_format'] = 'raw'
            # create different sasl_user name for different job
            params.update({'sasl_user': params.get("sasl_user") +
                           utils_misc.generate_random_string(3)})
            logging.info('sals user name is %s' % params.get("sasl_user"))

            # Create SASL user on the ovirt host
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

        uri = utils_v2v.Uri('xen').get_uri(xen_host)
        virsh_dargs = {'uri': uri,
                       'remote_ip': xen_host,
                       'remote_user': 'root',
                       'remote_pwd': xen_host_passwd,
                       'auto_close': True,
                       'debug': True}
        virsh_instance = utils_v2v.wait_for(virsh.VirshPersistent, **virsh_dargs)
        logging.debug('A new virsh session %s was created', virsh_instance)
        if not utils_v2v.wait_for(virsh_instance.domain_exists, name=vm_name):
            test.error('VM %s not exists' % vm_name)

        if checkpoint in bk_list:
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
                if line.strip().startswith(('hda', 'vda', 'sda', 'xvda')):
                    params['remote_disk_image'] = line.split()[-1]
                    break
            # Local path of disk image
            params['img_path'] = data_dir.get_tmp_dir() + '/%s.img' % vm_name
            if checkpoint == 'xvda_disk':
                v2v_params['input_mode'] = 'disk'
                v2v_params['hypervisor'] = 'kvm'
                v2v_params.update({'input_file': params['img_path']})
            # Copy remote image to local with scp
            remote.scp_from_remote(xen_host, 22, xen_host_user,
                                   xen_host_passwd,
                                   params['remote_disk_image'],
                                   params['img_path'])
        if checkpoint == 'pool_uuid':
            virsh.pool_start(pool_name)
            pooluuid = virsh.pool_uuid(pool_name).stdout.strip()
            v2v_params['os_pool'] = pooluuid
        if checkpoint.startswith('vnc'):
            vm_xml.VMXML.set_graphics_attr(vm_name, {'type': 'vnc'},
                                           virsh_instance=virsh_instance)
            if checkpoint == 'vnc_autoport':
                params[checkpoint] = {'autoport': 'yes'}
                vm_xml.VMXML.set_graphics_attr(vm_name, params[checkpoint],
                                               virsh_instance=virsh_instance)
            elif checkpoint in ['vnc_encrypt', 'vnc_encrypt_warning']:
                params[checkpoint] = {
                    'passwd': params.get(
                        'vnc_passwd', 'redhat')}
                vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(
                    vm_name, virsh_instance=virsh_instance)
                vm_xml.VMXML.add_security_info(
                    vmxml, params[checkpoint]['passwd'],
                    virsh_instance=virsh_instance)
            logging.debug(
                virsh_instance.dumpxml(
                    vm_name,
                    extra='--security-info'))
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
                logging.debug(process.run('cat %s' % xml_file).stdout_text)
        if checkpoint == 'ssh_banner':
            session = remote.remote_login("ssh", xen_host, "22", "root",
                                          xen_host_passwd, "#")
            ssh_banner_content = r'"# no default banner path\n' \
                                 r'#Banner /path/banner file\n' \
                                 r'Banner /etc/ssh/ssh_banner"'
            logging.info('Create ssh_banner file')
            session.cmd(
                'echo -e %s > /etc/ssh/ssh_banner' %
                ssh_banner_content)
            logging.info('Content of ssh_banner file:')
            logging.info(session.cmd_output('cat /etc/ssh/ssh_banner'))
            logging.info('Restart sshd service on xen host')
            session.cmd('service sshd restart')
        if checkpoint == 'cdrom':
            xml = vm_xml.VMXML.new_from_inactive_dumpxml(
                vm_name, virsh_instance=virsh_instance)
            logging.debug(xml.xmltreefile)
            disks = xml.get_disk_all_by_expr('device==cdrom')
            logging.debug('Disks: %r', disks)
            for disk in list(disks.values()):
                # Check if vm has cdrom attached
                if disk.find('source') is None:
                    test.error('No CDROM image attached')
        if checkpoint == 'vdsm':
            extra_pkg = params.get('extra_pkg')
            logging.info('Install %s', extra_pkg)
            utils_package.package_install(extra_pkg.split(','))

            # Backup conf file for recovery
            for conf in params['bk_conf'].strip().split(','):
                logging.debug('Back up %s', conf)
                shutil.copyfile(conf, conf + '.bk')

            logging.info('Configure libvirt for vdsm')
            process.run('vdsm-tool configure --force')

            logging.info('Start vdsm service')
            service_manager = service.Factory.create_generic_service()
            service_manager.start('vdsmd')

            # Setup user and password
            user_pwd = "[['%s', '%s']]" % (params.get("sasl_user"),
                                           params.get("sasl_pwd"))
            v2v_sasl = utils_sasl.SASL(sasl_user_pwd=user_pwd)
            v2v_sasl.server_ip = 'localhost'
            v2v_sasl.server_user = params.get('sasl_server_user', 'root')
            v2v_sasl.server_pwd = params.get('sasl_server_passwd')
            v2v_sasl.setup()
            logging.debug('A SASL session %s was created', v2v_sasl)

            v2v_params['sasl_user'] = params.get("sasl_user")
            v2v_params['sasl_pwd'] = params.get("sasl_pwd")
        if checkpoint == 'multidisk':
            params['disk_count'] = 0
            blklist = virsh.domblklist(vm_name, uri=uri).stdout.split('\n')
            logging.info(blklist)
            for line in blklist:
                if '/' in line:
                    params['disk_count'] += 1
            logging.info('Total disks: %d', params['disk_count'])

        # Execute virt-v2v
        v2v_result = utils_v2v.v2v_cmd(v2v_params)

        if new_vm_name:
            vm_name = new_vm_name
            params['main_vm'] = new_vm_name
        check_result(v2v_result, status_error)
    finally:
        logging.info("Cleaning Environment")
        # Cleanup constant files
        utils_v2v.cleanup_constant_files(params)
        if checkpoint == 'ssh_banner':
            logging.info('Remove ssh_banner file')
            session = remote.remote_login("ssh", xen_host, "22", "root",
                                          xen_host_passwd, "#")
            session.cmd('rm -f /etc/ssh/ssh_banner')
            session.cmd('service sshd restart')
        # Restore crypto-policies to DEFAULT, the setting is impossible to be
        # other values by default in testing envrionment.
        process.run(
            'update-crypto-policies --set DEFAULT',
            verbose=True,
            ignore_status=True,
            shell=True)
        if bk_xml:
            bk_xml.sync(virsh_instance=virsh_instance)
        if virsh_instance:
            logging.debug('virsh session %s is closing', virsh_instance)
            virsh_instance.close_session()
        if params.get('vmchecker'):
            params['vmchecker'].cleanup()
        if output_mode == 'rhev' and v2v_sasl:
            v2v_sasl.cleanup()
            logging.debug('SASL session %s is closing', v2v_sasl)
            v2v_sasl.close_session()
        if checkpoint == 'vdsm':
            logging.info('Stop vdsmd')
            service_manager = service.Factory.create_generic_service()
            service_manager.stop('vdsmd')
            if params.get('extra_pkg'):
                utils_package.package_remove(params['extra_pkg'].split(','))
            for conf in params['bk_conf'].strip().split(','):
                if os.path.exists(conf + '.bk'):
                    logging.debug('Recover %s', conf)
                    os.remove(conf)
                    shutil.move(conf + '.bk', conf)
            logging.info('Restart libvirtd')
            libvirtd = utils_libvirtd.Libvirtd()
            libvirtd.restart()
            logging.info('Start network "default"')
            virsh.net_start('default')
            virsh.undefine(vm_name)
        if output_mode == 'libvirt':
            pvt.cleanup_pool(pool_name, pool_type, pool_target, '')
        utils_v2v.v2v_setup_ssh_key_cleanup(xen_session, xen_pubkey)
        process.run('ssh-agent -k')
