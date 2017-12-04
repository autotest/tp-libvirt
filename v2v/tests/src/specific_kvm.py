import os
import re
import logging
import string

import aexpect

from avocado.utils import service
from avocado.utils import process

from virttest import virsh
from virttest import utils_v2v
from virttest import utils_misc
from virttest import utils_sasl
from virttest import libvirt_vm
from virttest import data_dir
from virttest import utils_selinux
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt as utlv

from provider.v2v_vmcheck_helper import VMChecker


def run(test, params, env):
    """
    convert specific kvm guest to rhev
    """
    for v in params.itervalues():
        if "V2V_EXAMPLE" in v:
            test.cancel("Please set real value for %s" % v)
    if utils_v2v.V2V_EXEC is None:
        raise ValueError('Missing command: virt-v2v')
    vm_name = params.get('main_vm', 'EXAMPLE')
    target = params.get('target')
    remote_host = params.get('remote_host', 'EXAMPLE')
    output_mode = params.get('output_mode')
    output_format = params.get('output_format')
    output_storage = params.get('output_storage', 'default')
    bridge = params.get('bridge')
    network = params.get('network')
    ntp_server = params.get('ntp_server')
    address_cache = env.get('address_cache')
    pool_name = params.get('pool_name', 'v2v_test')
    pool_type = params.get('pool_type', 'dir')
    pool_target = params.get('pool_target_path', 'v2v_pool')
    pvt = utlv.PoolVolumeTest(test, params)
    v2v_timeout = int(params.get('v2v_timeout', 1200))
    skip_check = 'yes' == params.get('skip_check', 'no')
    status_error = 'yes' == params.get('status_error', 'no')
    checkpoint = params.get('checkpoint', '')
    debug_kernel = 'debug_kernel' == checkpoint
    multi_kernel_list = ['multi_kernel', 'debug_kernel']
    backup_list = ['virtio_on', 'virtio_off', 'floppy', 'floppy_devmap',
                   'fstab_cdrom', 'fstab_virtio', 'multi_disks', 'sata_disk',
                   'network_virtio', 'network_rtl8139', 'network_e1000',
                   'multi_netcards', 'spice', 'spice_encrypt', 'spice_qxl',
                   'spice_cirrus', 'vnc_qxl', 'vnc_cirrus', 'blank_2nd_disk',
                   'listen_none', 'listen_socket', 'only_net', 'only_br']
    error_list = []

    def log_fail(msg):
        """
        Log error and update error list
        """
        logging.error(msg)
        error_list.append(msg)

    def vm_shell(func):
        """
        Decorator of shell session to vm
        """

        def wrapper(*args, **kwargs):
            vm = libvirt_vm.VM(vm_name, params, test.bindir,
                               env.get('address_cache'))
            if vm.is_dead():
                logging.info('VM is down. Starting it now.')
                vm.start()
            session = vm.wait_for_login()
            kwargs['session'] = session
            kwargs['vm'] = vm
            func(*args, **kwargs)
            if session:
                session.close()
            vm.shutdown()

        return wrapper

    def check_disks(vmcheck):
        """
        Check disk counts inside the VM
        """
        # Initialize windows boot up
        os_type = params.get("os_type", "linux")
        expected_disks = int(params.get("ori_disks", "1"))
        logging.debug("Expect %s disks im VM after convert", expected_disks)
        # Get disk counts
        if os_type == "linux":
            cmd = "lsblk |grep disk |wc -l"
            disks = int(vmcheck.session.cmd(cmd).strip())
        else:
            cmd = r"echo list disk > C:\list_disk.txt"
            vmcheck.session.cmd(cmd)
            cmd = r"diskpart /s C:\list_disk.txt"
            output = vmcheck.session.cmd(cmd).strip()
            logging.debug("Disks in VM: %s", output)
            disks = len(re.findall('Disk\s\d', output))
        logging.debug("Find %s disks in VM after convert", disks)
        if disks == expected_disks:
            logging.info("Disk counts is expected")
        else:
            log_fail("Disk counts is wrong")

    @vm_shell
    def check_vmlinuz_initramfs(v2v_result):
        """
        Check if vmlinuz matches initramfs on multi-kernel case
        """
        logging.info('Checking if vmlinuz matches initramfs')
        kernels = re.search(
            'kernel packages in this guest:.*?(\(kernel.*?\).*?){2,}',
            v2v_result, re.DOTALL)
        try:
            lines = kernels.group(0)
            kernel_list = re.findall('\((.*?)\)', lines)
            for kernel in kernel_list:
                vmlinuz = re.search(r'/boot/vmlinuz-(.*?),', kernel).group(1)
                initramfs = \
                    re.search(r'/boot/initramfs-(.*?)\.img', kernel).group(1)
                logging.debug('vmlinuz version is: %s' % vmlinuz)
                logging.debug('initramfs version is: %s' % initramfs)
                if vmlinuz != initramfs:
                    log_fail('vmlinuz not match with initramfs')
        except Exception, e:
            test.error('Error on find kernel info \n %s' % str(e))

    def check_boot_kernel(vmcheck):
        """
        Check if converted vm use the latest kernel
        """
        current_kernel = vmcheck.session.cmd('uname -r').strip()
        logging.debug('Current kernel: %s' % current_kernel)
        if current_kernel == '3.10.0-799.el7.x86_64':
            logging.debug('The kernel is the latest kernel')
        else:
            log_fail('VM should choose lastest kernel not %s' % current_kernel)

    def check_floppy_exist(vmcheck):
        """
        Check if floppy exists after convertion
        """
        blk = vmcheck.session.cmd('lsblk')
        logging.info(blk)
        if not re.search('fd0', blk):
            log_fail('Floppy not found')

    def attach_removable_media(type, source, dev):
        bus = {'cdrom': 'ide', 'floppy': 'fdc', 'disk': 'virtio'}
        args = {'driver': 'qemu', 'subdriver': 'raw', 'sourcetype': 'file',
                'type': type, 'targetbus': bus[type]}
        if type == 'cdrom':
            args.update({'mode': 'readonly'})
        config = ''
        # Join all options together to get command line
        for key in args.keys():
            config += ' --%s %s' % (key, args[key])
        config += ' --current'
        virsh.attach_disk(vm_name, source, dev, extra=config)

    def change_disk_bus(dest):
        """
        Change all disks' bus type to $dest
        """
        bus_list = ['ide', 'sata', 'virtio']
        if dest not in bus_list:
            test.error('Bus type not support')
        dev_prefix = ['h', 's', 'v']
        dev_table = dict(zip(bus_list, dev_prefix))
        logging.info('Change disk bus to %s' % dest)
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        disks = vmxml.get_disk_all()
        index = 0
        for disk in disks.values():
            if disk.get('device') != 'disk':
                continue
            target = disk.find('target')
            target.set('bus', dest)
            target.set('dev', dev_table[dest] + 'd' + string.lowercase[index])
            disk.remove(disk.find('address'))
            index += 1
        vmxml.sync()

    def change_network_model(model):
        """
        Change network model to $model
        """
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        network_list = vmxml.get_iface_all()
        for node in network_list.values():
            if node.get('type') == 'network':
                node.find('model').set('type', model)
        vmxml.sync()

    def attach_network_card(model):
        """
        Attach network card based on model
        """
        if model not in ('e1000', 'virtio', 'rtl8139'):
            test.error('Network model not support')
        options = {'type': 'network', 'source': 'default', 'model': model}
        line = ''
        for key in options:
            line += ' --' + key + ' ' + options[key]
        line += ' --current'
        logging.debug(virsh.attach_interface(vm_name, option=line))

    def check_multi_netcards(mac_list, virsh_instance):
        """
        Check if number and type of network cards meet expectation
        """
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(
            vm_name, virsh_instance=virsh_instance)
        iflist = vmxml.get_iface_all()
        logging.debug('MAC list before v2v: %s' % mac_list)
        logging.debug('MAC list after  v2v: %s' % iflist.keys())
        if set(mac_list).difference(iflist.keys()):
            log_fail('Missing network interface')
        for mac in iflist:
            if iflist[mac].find('model').get('type') != 'virtio':
                log_fail('Network not convert to virtio')

    @vm_shell
    def insert_floppy_devicemap(**kwargs):
        """
        Add an entry of floppy to device.map
        """
        session = kwargs['session']
        line = '(fd0)     /dev/fd0'
        devmap = '/boot/grub/device.map'
        if session.cmd_status('ls %s' % devmap):
            devmap = '/boot/grub2/device.map'
        cmd_exist = 'grep \'(fd0)\' %s' % devmap
        cmd_set = 'sed -i \'2i%s\' %s' % (line, devmap)
        if session.cmd_status(cmd_exist):
            session.cmd(cmd_set)

    def make_label(session):
        """
        Label a volume, swap or root volume
        """
        # swaplabel for rhel7 with xfs, e2label for rhel6 or ext*
        cmd_map = {'root': 'e2label %s ROOT',
                   'swap': 'swaplabel -L SWAPPER %s'}
        if not session.cmd_status('swaplabel --help'):
            blk = 'swap'
        elif not session.cmd_status('which e2label'):
            blk = 'root'
        else:
            test.error('No tool to make label')
        entry = session.cmd('blkid|grep %s' % blk).strip()
        path = entry.split()[0].strip(':')
        cmd_label = cmd_map[blk] % path
        if 'LABEL' not in entry:
            session.cmd(cmd_label)
        return blk

    @vm_shell
    def specify_fstab_entry(type, **kwargs):
        """
        Specify entry in fstab file
        """
        type_list = ['cdrom', 'uuid', 'label', 'virtio', 'sr0', 'invalid']
        if type not in type_list:
            test.error('Not support %s in fstab' % type)
        session = kwargs['session']
        # Specify cdrom device
        if type == 'cdrom':
            line = '/dev/cdrom /media/CDROM auto exec'
            if 'grub2' in utils_misc.get_bootloader_cfg(session):
                line += ',nofail'
            line += ' 0 0'
            logging.debug('fstab entry is "%s"', line)
            cmd = [
                'mkdir -p /media/CDROM',
                'mount /dev/cdrom /media/CDROM',
                'echo "%s" >> /etc/fstab' % line
            ]
            for i in range(len(cmd)):
                session.cmd(cmd[i])
        elif type == 'sr0':
            line = params.get('fstab_content')
            session.cmd('echo "%s" >> /etc/fstab' % line)
        elif type == 'invalid':
            line = utils_misc.generate_random_string(6)
            session.cmd('echo "%s" >> /etc/fstab' % line)
        else:
            map = {'uuid': 'UUID', 'label': 'LABEL', 'virtio': '/vd'}
            logging.info(type)
            if session.cmd_status('cat /etc/fstab|grep %s' % map[type]):
                # Specify device by UUID
                if type == 'uuid':
                    entry = session.cmd(
                        'blkid -s UUID|grep swap').strip().split()
                    # Replace path for UUID
                    origin = entry[0].strip(':')
                    replace = entry[1].replace('"', '')
                # Specify virtio device
                elif type == 'virtio':
                    entry = session.cmd('cat /etc/fstab|grep /boot').strip()
                    # Get the ID (no matter what, usually UUID)
                    origin = entry.split()[0]
                    key = origin.split('=')[1]
                    blkinfo = session.cmd('blkid|grep %s' % key).strip()
                    # Replace with virtio disk path
                    replace = blkinfo.split()[0].strip(':')
                # Specify device by label
                elif type == 'label':
                    blk = make_label(session)
                    entry = session.cmd('blkid|grep %s' % blk).strip()
                    # Remove " from LABEL="****"
                    replace = entry.split()[1].strip().replace('"', '')
                    # Replace the original id/path with label
                    origin = entry.split()[0].strip(':')
                cmd_fstab = "sed -i 's|%s|%s|' /etc/fstab" % (origin, replace)
                session.cmd(cmd_fstab)
        fstab = session.cmd_output('cat /etc/fstab')
        logging.debug('Content of /etc/fstab:\n%s', fstab)

    def create_large_file(session, left_space):
        """
        Create a large file to make left space of root less than $left_space MB
        """
        cmd_df = "df -m / --output=avail"
        df_output = session.cmd(cmd_df).strip()
        logging.debug('Command output: %s', df_output)
        avail = int(df_output.strip().split('\n')[-1])
        logging.info('Available space: %dM' % avail)
        if avail > left_space - 1:
            tmp_dir = data_dir.get_tmp_dir()
            if session.cmd_status('ls %s' % tmp_dir) != 0:
                session.cmd('mkdir %s' % tmp_dir)
            large_file = os.path.join(tmp_dir, 'file.large')
            cmd_create = 'dd if=/dev/zero of=%s bs=1M count=%d' % \
                         (large_file, avail - left_space + 2)
            session.cmd(cmd_create, timeout=v2v_timeout)
        logging.info('Available space: %sM' % session.cmd(cmd_df).strip())

    @vm_shell
    def corrupt_rpmdb(**kwargs):
        """
        Corrupt rpm db
        """
        session = kwargs['session']
        # If __db.* exist, remove them, then touch _db.001 to corrupt db.
        if not session.cmd_status('ls /var/lib/rpm/__db.001'):
            session.cmd('rm -f /var/lib/rpm/__db.*')
        session.cmd('touch /var/lib/rpm/__db.001')
        if not session.cmd_status('yum update'):
            test.error('Corrupt rpmdb failed')

    @vm_shell
    def grub_serial_terminal(**kwargs):
        """
        Edit the serial and terminal lines of grub.conf
        """
        session = kwargs['session']
        vm = kwargs['vm']
        grub_file = utils_misc.get_bootloader_cfg(session)
        if 'grub2' in grub_file:
            test.cancel('Skip this case on grub2')
        cmd = "sed -i '1iserial -unit=0 -speed=115200\\n"
        cmd += "terminal -timeout=10 serial console' %s" % grub_file
        session.cmd(cmd)

    @vm_shell
    def set_selinux(value, **kwargs):
        """
        Set selinux stat of guest
        """
        session = kwargs['session']
        current_stat = session.cmd_output('getenforce').strip()
        logging.debug('Current selinux status: %s', current_stat)
        if current_stat != value:
            cmd = "sed -E -i 's/(^SELINUX=).*?/\\1%s/' /etc/selinux/config" % value
            logging.info('Set selinux stat with command %s', cmd)
            session.cmd(cmd)

    @vm_shell
    def get_firewalld_status(**kwargs):
        """
        Return firewalld service status of vm
        """
        session = kwargs['session']
        firewalld_status = session.cmd('systemctl status firewalld.service|grep Active:',
                                       ok_status=[0, 3]).strip()
        logging.info('Status of firewalld: %s', firewalld_status)
        params[checkpoint] = firewalld_status

    def check_firewalld_status(vmcheck, expect_status):
        """
        Check if status of firewalld meets expectation
        """
        firewalld_status = vmcheck.session.cmd('systemctl status '
                                               'firewalld.service|grep Active:',
                                               ok_status=[0, 3]).strip()
        logging.info('Status of firewalld after v2v: %s', firewalld_status)
        if firewalld_status != expect_status:
            log_fail('Status of firewalld changed after conversion')

    @vm_shell
    def vm_cmd(cmd_list, **kwargs):
        """
        Excecute a list of commands on guest.
        """
        session = kwargs['session']
        for cmd in cmd_list:
            logging.info('Send command "%s"', cmd)
            status, output = session.cmd_status_output(cmd)
            logging.debug('Command output:\n%s', output)
            if status != 0:
                test.error('Command "%s" failed' % cmd)
        logging.info('All commands executed')

    def check_time_keep(vmcheck):
        """
        Check time drift after convertion.
        """
        logging.info('Check time drift')
        output = vmcheck.session.cmd('ntpdate -q %s' % ntp_server)
        logging.debug(output)
        drift = abs(float(output.split()[-2]))
        logging.debug('Time drift is: %f', drift)
        if drift > 3:
            log_fail('Time drift exceeds 3 sec')

    def check_boot():
        """
        Check if guest can boot up after configuration
        """
        try:
            vm = libvirt_vm.VM(vm_name, params, test.bindir,
                               env.get('address_cache'))
            if vm.is_alive():
                vm.shutdown()
            logging.info('Booting up %s' % vm_name)
            vm.start()
            vm.wait_for_login()
            vm.shutdown()
            logging.info('%s is down' % vm_name)
        except Exception, e:
            test.error('Bootup guest and login failed: %s', str(e))

    def check_result(result, status_error):
        """
        Check virt-v2v command result
        """
        utlv.check_exit_status(result, status_error)
        output = result.stdout + result.stderr
        if skip_check:
            logging.info('Skip checking vm after conversion')
        elif not status_error:
            if output_mode == 'rhev':
                if not utils_v2v.import_vm_to_ovirt(params, address_cache,
                                                    timeout=v2v_timeout):
                    test.fail('Import VM failed')
            if output_mode == 'libvirt':
                try:
                    virsh.start(vm_name, debug=True, ignore_status=False)
                except Exception, e:
                    test.fail('Start vm failed: %s' % str(e))
            # Check guest following the checkpoint document after convertion
            vmchecker = VMChecker(test, params, env)
            params['vmchecker'] = vmchecker
            if params.get('skip_check') != 'yes':
                ret = vmchecker.run()
                if len(ret) == 0:
                    logging.info("All common checkpoints passed")
            vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(
                vm_name, virsh_instance=vmchecker.virsh_instance)
            logging.debug(vmxml)
            if checkpoint == 'multi_kernel':
                check_boot_kernel(vmchecker.checker)
            if checkpoint == 'floppy':
                check_floppy_exist(vmchecker.checker)
            if checkpoint == 'multi_disks':
                check_disks(vmchecker.checker)
            if checkpoint == 'multi_netcards':
                check_multi_netcards(params['mac_address'],
                                     vmchecker.virsh_instance)
            if checkpoint.startswith(('spice', 'vnc')):
                if checkpoint == 'spice_encrypt':
                    vmchecker.check_graphics(params[checkpoint])
                else:
                    graph_type = checkpoint.split('_')[0]
                    vmchecker.check_graphics({'type': graph_type})
                    video_type = vmxml.get_devices('video')[0].model_type
                    if video_type.lower() != 'qxl':
                        log_fail('Video expect QXL, actual %s' % video_type)
            if checkpoint.startswith('listen'):
                listen_type = vmxml.get_devices('graphics')[0].listen_type
                logging.info('listen type is: %s', listen_type)
                if listen_type != checkpoint.split('_')[-1]:
                    log_fail('listen type changed after conversion')
            if checkpoint.startswith('selinux'):
                status = vmchecker.checker.session.cmd(
                    'getenforce').strip().lower()
                logging.info('Selinux status after v2v:%s', status)
                if status != checkpoint[8:]:
                    log_fail('Selinux status not match')
            if checkpoint == 'guest_firewalld_status':
                check_firewalld_status(vmchecker.checker, params[checkpoint])
            if checkpoint in ['ntpd_on', 'sync_ntp']:
                check_time_keep(vmchecker.checker)
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
            'hostname': remote_host, 'hypervisor': 'kvm', 'v2v_opts': '-v -x',
            'storage': output_storage, 'network': network, 'bridge': bridge,
            'target': target, 'main_vm': vm_name, 'input_mode': 'libvirt',
            'new_name': vm_name + '_' + utils_misc.generate_random_string(3)
        }
        if output_format:
            v2v_params.update({'output_format': output_format})
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
        if output_mode == 'local':
            v2v_params['storage'] = data_dir.get_tmp_dir()
        if output_mode == 'libvirt':
            pvt.pre_pool(pool_name, pool_type, pool_target, '')
        # Set libguestfs environment variable
        os.environ['LIBGUESTFS_BACKEND'] = 'direct'

        backup_xml = None
        if checkpoint in backup_list:
            backup_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        if checkpoint == 'multi_disks':
            attach_disk_path = os.path.join(test.tmpdir, 'attach_disks')
            utlv.attach_disks(env.get_vm(vm_name), attach_disk_path,
                              None, params)
            new_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
            disk_count = 0
            for disk in new_xml.get_disk_all().values():
                if disk.get('device') == 'disk':
                    disk_count += 1
            params['ori_disks'] = disk_count
        if checkpoint == 'virtio_on':
            change_disk_bus('virtio')
        if checkpoint == 'virtio_off':
            change_disk_bus('ide')
        if checkpoint == 'sata_disk':
            change_disk_bus('sata')
        if checkpoint.startswith('floppy'):
            img_path = data_dir.get_tmp_dir() + '/floppy.img'
            utlv.create_local_disk('floppy', img_path)
            attach_removable_media('floppy', img_path, 'fda')
            if checkpoint == 'floppy_devmap':
                insert_floppy_devicemap()
        if checkpoint.startswith('fstab'):
            if checkpoint == 'fstab_cdrom':
                img_path = data_dir.get_tmp_dir() + '/cdrom.iso'
                utlv.create_local_disk('iso', img_path)
                attach_removable_media('cdrom', img_path, 'hdc')
            if checkpoint == 'fstab_virtio':
                change_disk_bus('virtio')
            specify_fstab_entry(checkpoint[6:])
        if checkpoint == 'running':
            virsh.start(vm_name)
            logging.info('VM state: %s' %
                         virsh.domstate(vm_name).stdout.strip())
        if checkpoint == 'paused':
            virsh.start(vm_name, '--paused')
            logging.info('VM state: %s' %
                         virsh.domstate(vm_name).stdout.strip())
        if checkpoint == 'serial_terminal':
            grub_serial_terminal()
            check_boot()
        if checkpoint == 'no_space':
            @vm_shell
            def take_space(**kwargs):
                create_large_file(kwargs['session'], 20)
            take_space()
        if checkpoint.startswith('host_no_space'):
            session = aexpect.ShellSession('sh')
            create_large_file(session, 1000)
            if checkpoint == 'host_no_space_setcache':
                logging.info('Set LIBGUESTFS_CACHEDIR=/home')
                os.environ['LIBGUESTFS_CACHEDIR'] = '/home'
        if checkpoint == 'corrupt_rpmdb':
            corrupt_rpmdb()
        if checkpoint.startswith('network'):
            change_network_model(checkpoint[8:])
        if checkpoint == 'multi_netcards':
            attach_network_card('virtio')
            attach_network_card('e1000')
            params['mac_address'] = []
            vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
            network_list = vmxml.get_iface_all()
            for mac in network_list:
                if network_list[mac].get('type') == 'network':
                    params['mac_address'].append(mac)
            if len(params['mac_address']) < 2:
                test.error('Not enough network interface')
            logging.debug('MAC address: %s' % params['mac_address'])
        if checkpoint.startswith(('spice', 'vnc')):
            if checkpoint == 'spice_encrypt':
                spice_passwd = {'type': 'spice',
                                'passwd': params.get('spice_passwd', 'redhat')}
                vm_xml.VMXML.set_graphics_attr(vm_name, spice_passwd)
                params[checkpoint] = {'type': 'spice',
                                      'passwdValidTo': '1970-01-01T00:00:01'}
            else:
                graphic_video = checkpoint.split('_')
                graphic = graphic_video[0]
                logging.info('Set graphic type to %s', graphic)
                vm_xml.VMXML.set_graphics_attr(vm_name, {'type': graphic})
                if len(graphic_video) > 1:
                    video_type = graphic_video[1]
                    logging.info('Set video type to %s', video_type)
                    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
                    video = vmxml.xmltreefile.find(
                        'devices').find('video').find('model')
                    video.set('type', video_type)
                    vmxml.sync()
        if checkpoint.startswith('listen'):
            listen_type = checkpoint.split('_')[-1]
            vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
            listen = vmxml.xmltreefile.find(
                'devices').find('graphics').find('listen')
            listen.set('type', listen_type)
            vmxml.sync()
        if checkpoint == 'host_selinux_on':
            params['selinux_stat'] = utils_selinux.get_status()
            utils_selinux.set_status('enforcing')
        if checkpoint.startswith('selinux'):
            set_selinux(checkpoint[8:])
        if checkpoint.startswith('host_firewalld'):
            service_mgr = service.ServiceManager()
            logging.info('Backing up firewall services status')
            params['bk_firewalld_status'] = service_mgr.status('firewalld')
            if 'start' in checkpoint:
                service_mgr.start('firewalld')
            if 'stop' in checkpoint:
                service_mgr.stop('firewalld')
        if checkpoint == 'guest_firewalld_status':
            get_firewalld_status()
        if checkpoint == 'remove_securetty':
            logging.info('Remove /etc/securetty file from guest')
            cmd = ['rm -f /etc/securetty']
            vm_cmd(cmd)
        if checkpoint == 'ntpd_on':
            logging.info('Set service ntpd on')
            cmd = ['yum -y install ntp',
                   'systemctl start ntpd']
            vm_cmd(cmd)
        if checkpoint == 'sync_ntp':
            logging.info('Sync time with %s', ntp_server)
            cmd = ['yum -y install ntpdate',
                   'ntpdate %s' % ntp_server]
            vm_cmd(cmd)
        if checkpoint == 'blank_2nd_disk':
            disk_path = os.path.join(data_dir.get_tmp_dir(), 'blank.img')
            logging.info('Create blank disk %s', disk_path)
            process.run('truncate -s 1G %s' % disk_path)
            logging.info('Attach blank disk to vm')
            attach_removable_media('disk', disk_path, 'vdc')
            logging.debug(virsh.dumpxml(vm_name))
        if checkpoint in ['only_net', 'only_br']:
            logging.info('Detatch all networks')
            virsh.detach_interface(vm_name, 'network --current', debug=True)
            logging.info('Detatch all bridges')
            virsh.detach_interface(vm_name, 'bridge --current', debug=True)
        if checkpoint == 'only_net':
            logging.info('Attach network')
            virsh.attach_interface(
                vm_name, 'network default --current', debug=True)
            v2v_params.pop('bridge')
        if checkpoint == 'only_br':
            logging.info('Attatch bridge')
            virsh.attach_interface(
                vm_name, 'bridge virbr0 --current', debug=True)
            v2v_params.pop('network')
        if checkpoint == 'no_libguestfs_backend':
            os.environ.pop('LIBGUESTFS_BACKEND')
        if checkpoint == 'file_image':
            vm = env.get_vm(vm_name)
            disk = vm.get_first_disk_devices()
            logging.info('Disk type is %s', disk['type'])
            if disk['type'] != 'file':
                test.error('Guest is not with file image')
        virsh.dumpxml(vm_name, debug=True)
        v2v_result = utils_v2v.v2v_cmd(v2v_params)
        if v2v_params.get('new_name'):
            vm_name = params['main_vm'] = v2v_params['new_name']
        check_result(v2v_result, status_error)
    finally:
        if params.get('vmchecker'):
            params['vmchecker'].cleanup()
        if output_mode == 'libvirt':
            pvt.cleanup_pool(pool_name, pool_type, pool_target, '')
        if backup_xml:
            backup_xml.sync()
        if params.get('selinux_stat') and params['selinux_stat'] != 'disabled':
            utils_selinux.set_status(params['selinux_stat'])
        if 'bk_firewalld_status' in params:
            service_mgr = service.ServiceManager()
            if service_mgr.status('firewalld') != params['bk_firewalld_status']:
                if params['bk_firewalld_status']:
                    service_mgr.start('firewalld')
                else:
                    service_mgr.stop('firewalld')
        if checkpoint.startswith('host_no_space'):
            large_file = os.path.join(data_dir.get_tmp_dir(), 'file.large')
            if os.path.isfile(large_file):
                os.remove(large_file)
