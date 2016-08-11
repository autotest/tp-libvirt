import os
import re
import logging
import string

from avocado.core import exceptions
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
            raise exceptions.TestSkipError("Please set real value for %s" % v)
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
    v2v_timeout = int(params.get('v2v_timeout', 1200))
    status_error = 'yes' == params.get('status_error', 'no')
    checkpoint = params.get('checkpoint', '')
    debug_kernel = 'debug_kernel' == checkpoint
    multi_kernel_list = ['multi_kernel', 'debug_kernel']
    backup_list = ['virtio_on', 'virtio_off', 'floppy', 'floppy_devmap',
                   'fstab_cdrom', 'fstab_virtio', 'multi_disks', 'sata_disk',
                   'network_virtio', 'network_rtl8139', 'network_e1000',
                   'multi_netcards', 'spice', 'spice_encrypt', 'blank_2nd_disk']
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

    def install_kernel(session, url=None, kernel_debug=False):
        """
        Install kernel to vm
        """
        if kernel_debug:
            if not utils_misc.yum_install(['kernel-debug'], session=session):
                raise exceptions.TestError('Fail on installing debug kernel')
            else:
                logging.info('Install kernel-debug success')
        else:
            if not (url and url.endswith('.rpm')):
                raise exceptions.TestError('kernel url not contain ".rpm"')
            # rhel6 need to install kernel-firmware first
            if '.el6' in session.cmd('uname -r'):
                kernel_fm_url = params.get('kernel_fm_url')
                cmd_install_firmware = 'rpm -Uv %s --force' % kernel_fm_url
                try:
                    session.cmd(cmd_install_firmware, timeout=v2v_timeout)
                except Exception, e:
                    raise exceptions.TestError(str(e))
            cmd_install_kernel = 'rpm -iv %s --force' % url
            try:
                session.cmd(cmd_install_kernel, timeout=v2v_timeout)
            except Exception, e:
                raise exceptions.TestError(str(e))

    @vm_shell
    def multi_kernel(*args, **kwargs):
        """
        Make multi-kernel test
        """
        session = kwargs['session']
        vm = kwargs['vm']
        kernel_url = params.get('kernel_url')
        install_kernel(session, kernel_url, debug_kernel)
        default_kernel = vm.set_boot_kernel(1, debug_kernel)
        if not default_kernel:
            raise exceptions.TestError('Set default kernel failed')
        params['defaultkernel'] = default_kernel

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
            raise exceptions.TestError('Error on find kernel info \n %s' % str(e))

    def check_boot_kernel(vmcheck, default_kernel, kernel_debug=False):
        """
        Check if converted vm use the default kernel
        """
        logging.debug('Check debug kernel: %s' % kernel_debug)
        current_kernel = vmcheck.session.cmd('uname -r').strip()
        logging.debug('Current kernel: %s' % current_kernel)
        logging.debug('Default kernel: %s' % default_kernel)
        if kernel_debug:
            if '.debug' in current_kernel:
                log_fail('VM should choose non-debug kernel')
        elif current_kernel not in default_kernel:
            log_fail('VM should choose default kernel')

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
            raise exceptions.TestError('Bus type not support')
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
            raise exceptions.TestError('Network model not support')
        options = {'type': 'network', 'source': 'default', 'model': model}
        line = ''
        for key in options:
            line += ' --' + key + ' ' + options[key]
        line += ' --current'
        logging.debug(virsh.attach_interface(vm_name, option=line))

    def check_multi_netcards(mac_list, virsh_session_id):
        """
        Check if number and type of network cards meet expectation
        """
        virsh_instance = virsh.VirshPersistent(session_id=virsh_session_id)
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
            raise exceptions.TestError('No tool to make label')
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
        type_list = ['cdrom', 'uuid', 'label', 'virtio', 'invalid']
        if type not in type_list:
            raise exceptions.TestError('Not support %s in fstab' % type)
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
        elif type == 'invalid':
            line = utils_misc.generate_random_string(6)
            session.cmd('echo "%s" >> /etc/fstab' % line)
        else:
            map = {'uuid': 'UUID', 'label': 'LABEL', 'virtio': '/vd'}
            logging.info(type)
            if session.cmd_status('cat /etc/fstab|grep %s' % map[type]):
                # Specify device by UUID
                if type == 'uuid':
                    entry = session.cmd('blkid -s UUID|grep swap').strip().split()
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

    @vm_shell
    def create_large_file(**kwargs):
        """
        Create a large file to make left space of root less than 20m
        """
        session = kwargs['session']
        cmd_df = "df -m /|awk 'END{print $4}'"
        avail = int(session.cmd(cmd_df).strip())
        logging.info('Available space: %dM' % avail)
        if avail > 19:
            params['large_file'] = '/file.large'
            cmd_create = 'dd if=/dev/zero of=%s bs=1M count=%d' % \
                         (params['large_file'], avail - 18)
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
            raise exceptions.TestError('Corrupt rpmdb failed')

    @vm_shell
    def bogus_kernel(**kwargs):
        """
        Add a bogus kernel entry
        """
        session = kwargs['session']
        vm = kwargs['vm']
        grub_file = utils_misc.get_bootloader_cfg(session)
        cfg = {
            "file": [grub_file, "/etc/grub.d/40_custom"],
            "search": ["title .*?.img", "menuentry '.*?}"],
            "title": [["(title\s)", r"\1bogus "],
                      ["(menuentry\s'.*?)'", r"\1 bogus'"]],
            "kernel": [["(kernel .*?)(\s)", r"\1.bogus\2"],
                       ["(/vmlinuz.*?)(\s)", r"\1.bogus\2"]],
            "make": ["pwd", "grub2-mkconfig -o /boot/grub2/grub.cfg"]
        }
        if 'grub2' in grub_file:
            index = 1
        else:
            index = 0
        content = session.cmd('cat %s' % grub_file).strip()
        search = re.search(cfg['search'][index], content, re.DOTALL)
        if search:
            # Make a copy of existing kernel entry string
            new_entry = search.group(0)
            # Replace title with bogus title
            new_entry = re.sub(cfg['title'][index][0],
                               cfg['title'][index][1], new_entry)
            # Replace kernel with bogus kernel
            new_entry = re.sub(cfg['kernel'][index][0],
                               cfg['kernel'][index][1], new_entry)
            logging.info(new_entry)
            session.cmd('echo "%s"|cat >> %s' % (new_entry, cfg['file'][index]))
            # Make effect
            session.cmd(cfg['make'][index])
        else:
            raise exceptions.TestError('No kernel found')

    @vm_shell
    def grub_serial_terminal(**kwargs):
        """
        Edit the serial and terminal lines of grub.conf
        """
        session = kwargs['session']
        vm = kwargs['vm']
        grub_file = utils_misc.get_bootloader_cfg(session)
        if 'grub2' in grub_file:
            raise exceptions.TestSkipError('Skip this case on grub2')
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
                raise exceptions.TestError('Command "%s" failed' % cmd)
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

    def check_v2v_log(output, check=None):
        """
        Check if error/warning meets expectation
        """
        # Fail if found error message
        not_expect_map = {
            'fstab_cdrom': ['warning: /files/etc/fstab.*? references unknown'
                            ' device "cdrom"'],
            'fstab_label': ['unknown filesystem label.*'],
            'fstab_uuid': ['unknown filesystem UUID.*'],
            'fstab_virtio': ['unknown filesystem /dev/vd.*'],
            'kdump': ['.*multiple files in /boot could be the initramfs.*'],
            'ctemp': ['.*case_sensitive_path: v2v: no file or directory.*'],
            'floppy_devmap': ['unknown filesystem /dev/fd'],
            'corrupt_rpmdb': ['.*error: rpmdb:.*']
        }
        # Fail if NOT found error message
        expect_map = {
            'not_shutdown': [
                '.*is running or paused.*',
                'virt-v2v: error: internal error: invalid argument:.*'
            ],
            'serial_terminal': ['virt-v2v: error: no kernels were found in '
                                'the grub configuration'],
            'no_space': ["virt-v2v: error: not enough free space for "
                         "conversion on filesystem '/'"],
            'unclean_fs': ['.*Windows Hibernation or Fast Restart.*'],
            'fstab_invalid': ['libguestfs error: /etc/fstab:.*?: augeas parse failure:']
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
                log_fail('Check v2v log failed')

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
            raise exceptions.TestError('Bootup guest and login failed: %s', str(e))

    def check_result(result, status_error):
        """
        Check virt-v2v command result
        """
        utlv.check_exit_status(result, status_error)
        output = result.stdout + result.stderr
        if status_error:
            if checkpoint in ['running', 'paused']:
                check_v2v_log(output, 'not_shutdown')
            else:
                check_v2v_log(output, checkpoint)
        else:
            if output_mode == 'rhev':
                if not utils_v2v.import_vm_to_ovirt(params, address_cache,
                                                    timeout=v2v_timeout):
                    raise exceptions.TestFail('Import VM failed')
            if output_mode == 'libvirt':
                try:
                    virsh.start(vm_name, debug=True, ignore_status=False)
                except Exception, e:
                    raise exceptions.TestFail('Start vm failed: %s' % str(e))
            # Check guest following the checkpoint document after convertion
            vmchecker = VMChecker(test, params, env)
            params['vmchecker'] = vmchecker
            ret = vmchecker.run()
            if len(ret) == 0:
                logging.info("All common checkpoints passed")
            if checkpoint in ['multi_kernel', 'debug_kernel']:
                default_kernel = params.get('defaultkernel')
                check_boot_kernel(vmchecker.checker, default_kernel, debug_kernel)
                if checkpoint == 'multi_kernel':
                    check_vmlinuz_initramfs(output)
            elif checkpoint == 'floppy':
                check_floppy_exist(vmchecker.checker)
            elif checkpoint == 'multi_disks':
                check_disks(vmchecker.checker)
            elif checkpoint == 'multi_netcards':
                check_multi_netcards(params['mac_address'],
                                     vmchecker.virsh_session_id)
            elif checkpoint.startswith('spice'):
                vmchecker.check_graphics({'type': 'spice'})
                if checkpoint == 'spice_encrypt':
                    vmchecker.check_graphics(params[checkpoint])
            elif checkpoint.startswith('selinux'):
                status = vmchecker.checker.session.cmd('getenforce').strip().lower()
                logging.info('Selinux status after v2v:%s', status)
                if status != checkpoint[8:]:
                    log_fail('Selinux status not match')
            elif checkpoint == 'guest_firewalld_status':
                check_firewalld_status(vmchecker.checker, params[checkpoint])
            elif checkpoint in ['ntpd_on', 'sync_ntp']:
                check_time_keep(vmchecker.checker)
            check_v2v_log(output, checkpoint)
            # Merge 2 error lists
            error_list.extend(vmchecker.errors)
            if len(error_list):
                log_fail('%d checkpoints failed: %s' % (len(error_list), error_list))

    try:
        v2v_params = {
            'hostname': remote_host, 'hypervisor': 'kvm', 'v2v_opts': '-v -x',
            'storage': output_storage, 'network': network, 'bridge': bridge,
            'target': target, 'main_vm': vm_name, 'input_mode': 'libvirt',
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
        elif checkpoint in multi_kernel_list:
            multi_kernel()
        elif checkpoint == 'virtio_on':
            change_disk_bus('virtio')
        elif checkpoint == 'virtio_off':
            change_disk_bus('ide')
        elif checkpoint == 'sata_disk':
            change_disk_bus('sata')
        elif checkpoint.startswith('floppy'):
            img_path = data_dir.get_tmp_dir() + '/floppy.img'
            utlv.create_local_disk('floppy', img_path)
            attach_removable_media('floppy', img_path, 'fda')
            if checkpoint == 'floppy_devmap':
                insert_floppy_devicemap()
        elif checkpoint.startswith('fstab'):
            if checkpoint == 'fstab_cdrom':
                img_path = data_dir.get_tmp_dir() + '/cdrom.iso'
                utlv.create_local_disk('iso', img_path)
                attach_removable_media('cdrom', img_path, 'hdc')
            elif checkpoint == 'fstab_virtio':
                change_disk_bus('virtio')
            specify_fstab_entry(checkpoint[6:])
        elif checkpoint == 'running':
            virsh.start(vm_name)
            logging.info('VM state: %s' %
                         virsh.domstate(vm_name).stdout.strip())
        elif checkpoint == 'paused':
            virsh.start(vm_name, '--paused')
            logging.info('VM state: %s' %
                         virsh.domstate(vm_name).stdout.strip())
        elif checkpoint == 'serial_terminal':
            grub_serial_terminal()
            check_boot()
        elif checkpoint == 'no_space':
            create_large_file()
        elif checkpoint == 'corrupt_rpmdb':
            corrupt_rpmdb()
        elif checkpoint == 'bogus_kernel':
            bogus_kernel()
            check_boot()
        elif checkpoint.startswith('network'):
            change_network_model(checkpoint[8:])
        elif checkpoint == 'multi_netcards':
            attach_network_card('virtio')
            attach_network_card('e1000')
            params['mac_address'] = []
            vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
            network_list = vmxml.get_iface_all()
            for mac in network_list:
                if network_list[mac].get('type') == 'network':
                    params['mac_address'].append(mac)
            if len(params['mac_address']) < 2:
                raise exceptions.TestError('Not enough network interface')
            logging.debug('MAC address: %s' % params['mac_address'])
        elif checkpoint.startswith('spice'):
            vm_xml.VMXML.set_graphics_attr(vm_name, {'type': 'spice'})
            if checkpoint == 'spice_encrypt':
                spice_passwd = {'passwd': params.get('spice_passwd', 'redhat')}
                vm_xml.VMXML.set_graphics_attr(vm_name, spice_passwd)
                params[checkpoint] = {'passwdValidTo': '1970-01-01T00:00:01'}
        elif checkpoint == 'host_selinux_on':
            params['selinux_stat'] = utils_selinux.get_status()
            utils_selinux.set_status('enforcing')
        elif checkpoint.startswith('selinux'):
            set_selinux(checkpoint[8:])
        elif checkpoint.startswith('host_firewalld'):
            service_mgr = service.ServiceManager()
            logging.info('Backing up firewall services status')
            params['bk_firewalld_status'] = service_mgr.status('firewalld')
            if 'start' in checkpoint:
                service_mgr.start('firewalld')
            if 'stop' in checkpoint:
                service_mgr.stop('firewalld')
        elif checkpoint == 'guest_firewalld_status':
            get_firewalld_status()
        elif checkpoint == 'remove_securetty':
            logging.info('Remove /etc/securetty file from guest')
            cmd = ['rm -f /etc/securetty']
            vm_cmd(cmd)
        elif checkpoint == 'ntpd_on':
            logging.info('Set service ntpd on')
            cmd = ['yum -y install ntp',
                   'systemctl start ntpd']
            vm_cmd(cmd)
        elif checkpoint == 'sync_ntp':
            logging.info('Sync time with %s', ntp_server)
            cmd = ['yum -y install ntpdate',
                   'ntpdate %s' % ntp_server]
            vm_cmd(cmd)
        elif checkpoint == 'blank_2nd_disk':
            disk_path = os.path.join(data_dir.get_tmp_dir(), 'blank.img')
            logging.info('Create blank disk %s', disk_path)
            process.run('truncate -s 1G %s' % disk_path)
            logging.info('Attach blank disk to vm')
            attach_removable_media('disk', disk_path, 'vdc')
            logging.debug(virsh.dumpxml(vm_name))

        v2v_result = utils_v2v.v2v_cmd(v2v_params)
        check_result(v2v_result, status_error)
    finally:
        if params.get('vmchecker'):
            params['vmchecker'].cleanup()
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
