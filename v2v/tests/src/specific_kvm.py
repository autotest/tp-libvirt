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
from virttest import remote
from virttest import xml_utils
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt as utlv

from provider.v2v_vmcheck_helper import VMChecker
from provider.v2v_vmcheck_helper import check_json_output
from provider.v2v_vmcheck_helper import check_local_output


def run(test, params, env):
    """
    convert specific kvm guest to rhev
    """
    for v in list(params.values()):
        if "V2V_EXAMPLE" in v:
            test.cancel("Please set real value for %s" % v)
    if utils_v2v.V2V_EXEC is None:
        raise ValueError('Missing command: virt-v2v')
    hypervisor = params.get("hypervisor")
    vm_name = params.get('main_vm', 'EXAMPLE')
    target = params.get('target')
    remote_host = params.get('remote_host', 'EXAMPLE')
    input_mode = params.get("input_mode")
    output_mode = params.get('output_mode')
    output_format = params.get('output_format')
    source_user = params.get("username", "root")
    os_pool = storage = params.get('output_storage')
    bridge = params.get('bridge')
    network = params.get('network')
    ntp_server = params.get('ntp_server')
    vpx_dc = params.get("vpx_dc")
    esx_ip = params.get("esx_hostname")
    address_cache = env.get('address_cache')
    pool_name = params.get('pool_name', 'v2v_test')
    pool_type = params.get('pool_type', 'dir')
    pool_target = params.get('pool_target_path', 'v2v_pool')
    pvt = utlv.PoolVolumeTest(test, params)
    v2v_opts = '-v -x' if params.get('v2v_debug',
                                     'on') in ['on', 'force_on'] else ''
    if params.get("v2v_opts"):
        # Add a blank by force
        v2v_opts += ' ' + params.get("v2v_opts")
    v2v_timeout = int(params.get('v2v_timeout', 3600))
    skip_vm_check = params.get('skip_vm_check', 'no')
    status_error = 'yes' == params.get('status_error', 'no')
    checkpoint = params.get('checkpoint', '')
    debug_kernel = 'debug_kernel' == checkpoint
    backup_list = ['floppy', 'floppy_devmap', 'fstab_cdrom',
                   'sata_disk', 'network_rtl8139', 'network_e1000',
                   'spice', 'spice_encrypt', 'spice_qxl',
                   'spice_cirrus', 'vnc_qxl', 'vnc_cirrus', 'blank_2nd_disk',
                   'listen_none', 'listen_socket', 'only_net', 'only_br']
    error_list = []

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

    # For VDDK
    input_transport = params.get("input_transport")
    vddk_libdir = params.get('vddk_libdir')
    # nfs mount source
    vddk_libdir_src = params.get('vddk_libdir_src')
    vddk_thumbprint = params.get('vddk_thumbprint')

    # Prepare step for different hypervisor
    if hypervisor == "xen":
        # See man virt-v2v-input-xen(1)
        process.run(
            'update-crypto-policies --set LEGACY',
            verbose=True,
            ignore_status=True,
            shell=True)

    if hypervisor == "esx":
        source_ip = params.get("vpx_hostname")
        source_pwd = params.get("vpx_password")
        vpx_passwd_file = params.get("vpx_passwd_file")
        # Create password file to access ESX hypervisor
        with open(vpx_passwd_file, 'w') as f:
            f.write(source_pwd)
    elif hypervisor == "xen":
        source_ip = params.get("xen_hostname")
        source_pwd = params.get("xen_host_passwd")
        # Set up ssh access using ssh-agent and authorized_keys
        xen_pubkey, xen_session = utils_v2v.v2v_setup_ssh_key(
            source_ip, source_user, source_pwd, auto_close=False)
        try:
            utils_misc.add_identities_into_ssh_agent()
        except Exception as e:
            process.run("ssh-agent -k")
            test.error("Fail to setup ssh-agent \n %s" % str(e))
    elif hypervisor == "kvm":
        source_ip = None
        source_pwd = None
    else:
        test.cancel("Unspported hypervisor: %s" % hypervisor)

    # Create libvirt URI
    v2v_uri = utils_v2v.Uri(hypervisor)
    remote_uri = v2v_uri.get_uri(source_ip, vpx_dc, esx_ip)
    logging.debug("libvirt URI for converting: %s", remote_uri)

    # Make sure the VM exist before convert
    v2v_virsh = None
    close_virsh = False
    if hypervisor == 'kvm':
        v2v_virsh = virsh
    else:
        virsh_dargs = {'uri': remote_uri,
                       'remote_ip': source_ip,
                       'remote_user': source_user,
                       'remote_pwd': source_pwd,
                       'auto_close': True,
                       'debug': True}
        v2v_virsh = virsh.VirshPersistent(**virsh_dargs)
        logging.debug('a new virsh session %s was created', v2v_virsh)
        close_virsh = True
    if not v2v_virsh.domain_exists(vm_name):
        test.error("VM '%s' not exist" % vm_name)

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
            disks = len(re.findall(r'Disk\s\d', output))
        logging.debug("Find %s disks in VM after convert", disks)
        if disks == expected_disks:
            logging.info("Disk counts is expected")
        else:
            log_fail("Disk counts is wrong")

    def check_vmlinuz_initramfs(v2v_output):
        """
        Check if vmlinuz matches initramfs on multi-kernel case
        """
        logging.debug('Checking if vmlinuz matches initramfs')
        kernel_strs = re.findall(
            r'(\* kernel.*?\/boot\/config){1,}',
            v2v_output,
            re.DOTALL)
        if len(kernel_strs) == 0:
            test.error("Not find kernel information")

        # Remove duplicate items by set
        logging.debug('Boots and kernel info: %s' % set(kernel_strs))
        for str_i in set(kernel_strs):
            # Fine all versions
            kernel_vers = re.findall(
                r'((?:\d+\.){1,}\d+-(?:\d+\.){1,}\w+)', str_i)
            logging.debug('kernel related versions: %s' % kernel_vers)
            # kernel_vers = [kernel, vmlinuz, initramfs] and they should be
            # same
            if len(kernel_vers) < 3 or len(set(kernel_vers)) != 1:
                log_fail("kernel versions does not match: %s" % kernel_vers)

    def check_boot_kernel(vmcheck):
        """
        Check if converted vm use the latest kernel
        """
        _, current_kernel = vmcheck.run_cmd('uname -r')

        if 'debug' in current_kernel:
            log_fail('Current kernel is a debug kernel: %s' % current_kernel)

        # 'sort -V' can satisfy our testing, even though it's not strictly perfect.
        # The last one is always the latest kernel version
        kernel_normal_list = vmcheck.run_cmd(
            'rpm -q kernel | sort -V')[1].strip().splitlines()
        status, kernel_debug = vmcheck.run_cmd('rpm -q kernel-debug')
        if status != 0:
            test.error('Not found kernel-debug package')
        all_kernel_list = kernel_normal_list + kernel_debug.strip().splitlines()
        logging.debug('All kernels: %s' % all_kernel_list)
        if len(all_kernel_list) < 3:
            test.error(
                'Needs at least 2 normal kernels and 1 debug kernel in VM')

        # The latest non-debug kernel must be kernel_normal_list[-1]
        if current_kernel.strip() != kernel_normal_list[-1].lstrip('kernel-'):
            log_fail('Check boot kernel failed')

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
        for key in list(args.keys()):
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
        dev_table = dict(list(zip(bus_list, dev_prefix)))
        logging.info('Change disk bus to %s' % dest)
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        disks = vmxml.get_disk_all_by_expr('device==disk')
        index = 0
        for disk in list(disks.values()):
            if disk.get('device') != 'disk':
                continue
            target = disk.find('target')
            target.set('bus', dest)
            target.set(
                'dev',
                dev_table[dest] +
                'd' +
                string.ascii_lowercase[index])
            disk.remove(disk.find('address'))
            index += 1
        vmxml.sync()

    def change_network_model(model):
        """
        Change network model to $model
        """
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        network_list = vmxml.get_iface_all()
        for node in list(network_list.values()):
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

    def check_multi_netcards(mac_list, vmxml):
        """
        Check if number and type of network cards meet expectation
        """
        xmltree = xml_utils.XMLTreeFile(vmxml)
        iface_nodes = xmltree.find('devices').findall('interface')
        iflist = {}
        for node in iface_nodes:
            mac_addr = node.find('mac').get('address')
            iflist[mac_addr] = node

        logging.debug('MAC list before v2v: %s' % mac_list)
        logging.debug('MAC list after  v2v: %s' % list(iflist.keys()))
        if set(mac_list).difference(list(iflist.keys())):
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
        type_list = ['cdrom', 'uuid', 'label', 'sr0', 'invalid']
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
            map = {'uuid': 'UUID', 'label': 'LABEL'}
            logging.info(type)
            if session.cmd_status('cat /etc/fstab|grep %s' % map[type]):
                # Specify device by UUID
                if type == 'uuid':
                    entry = session.cmd(
                        'blkid -s UUID|grep swap').strip().split()
                    # Replace path for UUID
                    origin = entry[0].strip(':')
                    replace = entry[1].replace('"', '')
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
        # Example: Active: active (running) since Fri 2019-03-15 01:03:39 CST;
        # 3min 48s ago
        firewalld_status = session.cmd(
            'systemctl status firewalld.service|grep Active:',
            ok_status=[
                0,
                3]).strip()
        # Exclude the time string because time changes if vm restarts
        firewalld_status = re.search(
            r'Active:\s\w*\s\(\w*\)',
            firewalld_status).group()
        logging.info('Status of firewalld: %s', firewalld_status)
        params[checkpoint] = firewalld_status

    def check_firewalld_status(vmcheck, expect_status):
        """
        Check if status of firewalld meets expectation
        """
        firewalld_status = vmcheck.session.cmd(
            'systemctl status '
            'firewalld.service|grep Active:', ok_status=[
                0, 3]).strip()
        # Exclude the time string because time changes if vm restarts
        firewalld_status = re.search(
            r'Active:\s\w*\s\(\w*\)',
            firewalld_status).group()
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
            # 'chronyc waitsync' needs more than 2mins to sync clock,
            # We set timeout to 300s will not have side-effects for other
            # commands.
            status, output = session.cmd_status_output(cmd, timeout=300)
            logging.debug('Command output:\n%s', output)
            if status != 0:
                test.error('Command "%s" failed' % cmd)
        logging.info('All commands executed')

    def check_time_keep(vmcheck):
        """
        Check time drift after convertion.
        """
        logging.info('Check time drift')
        output = vmcheck.session.cmd('chronyc tracking')
        logging.debug(output)
        if 'Not synchronised' in output:
            log_fail('Time not synchronised')
        lst_offset = re.search('Last offset *?: *(.*) ', output).group(1)
        drift = abs(float(lst_offset))
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
        except Exception as e:
            test.error('Bootup guest and login failed: %s' % str(e))

    def check_result(result, status_error):
        """
        Check virt-v2v command result
        """
        utlv.check_exit_status(result, status_error)
        output = result.stdout_text + result.stderr_text
        if not status_error:
            if output_mode == 'json' and not check_json_output(params):
                test.fail('check json output failed')
            if output_mode == 'local' and not check_local_output(params):
                test.fail('check local output failed')
            if output_mode in ['null', 'json', 'local']:
                return

            vmchecker = VMChecker(test, params, env)
            params['vmchecker'] = vmchecker
            if output_mode == 'rhev':
                if not utils_v2v.import_vm_to_ovirt(params, address_cache,
                                                    timeout=v2v_timeout):
                    test.fail('Import VM failed')
            if output_mode == 'libvirt':
                try:
                    virsh.start(vm_name, debug=True, ignore_status=False)
                except Exception as e:
                    test.fail('Start vm failed: %s' % str(e))
            # Check guest following the checkpoint document after convertion
            if params.get('skip_vm_check') != 'yes':
                ret = vmchecker.run()
                if len(ret) == 0:
                    logging.info("All common checkpoints passed")
            logging.debug(vmchecker.vmxml)
            if checkpoint == 'multi_kernel':
                check_boot_kernel(vmchecker.checker)
                check_vmlinuz_initramfs(output)
            if checkpoint == 'floppy':
                # Convert to rhv will remove all removeable devices(floppy,
                # cdrom)
                if output_mode in ['local', 'libvirt']:
                    check_floppy_exist(vmchecker.checker)
            if checkpoint == 'multi_disks':
                check_disks(vmchecker.checker)
            if checkpoint == 'multi_netcards':
                check_multi_netcards(params['mac_address'],
                                     vmchecker.vmxml)
            if checkpoint.startswith(('spice', 'vnc')):
                if checkpoint == 'spice_encrypt':
                    vmchecker.check_graphics(params[checkpoint])
                else:
                    graph_type = checkpoint.split('_')[0]
                    vmchecker.check_graphics({'type': graph_type})
                    video_type = vmchecker.xmltree.find(
                        './devices/video/model').get('type')
                    if video_type.lower() != 'qxl':
                        log_fail('Video expect QXL, actual %s' % video_type)
            if checkpoint.startswith('listen'):
                listen_type = vmchecker.xmltree.find(
                    './devices/graphics/listen').get('type')
                logging.info('listen type is: %s', listen_type)
                if listen_type != checkpoint.split('_')[-1]:
                    log_fail('listen type changed after conversion')
            if checkpoint.startswith('selinux'):
                status = vmchecker.checker.session.cmd(
                    'getenforce').strip().lower()
                logging.info('Selinux status after v2v:%s', status)
                if status != checkpoint[8:]:
                    log_fail('Selinux status not match')
            if checkpoint == 'check_selinuxtype':
                expect_output = vmchecker.checker.session.cmd(
                    'cat /etc/selinux/config')
                expect_selinuxtype = re.search(
                    r'^SELINUXTYPE=\s*(\S+)$', expect_output, re.MULTILINE).group(1)
                actual_output = vmchecker.checker.session.cmd('sestatus')
                actual_selinuxtype = re.search(
                    r'^Loaded policy name:\s*(\S+)$',
                    actual_output,
                    re.MULTILINE).group(1)
                if actual_selinuxtype != expect_selinuxtype:
                    log_fail('Seliunx type not match')
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
        v2v_sasl = None

        v2v_params = {
            'target': target,
            'hypervisor': hypervisor,
            'main_vm': vm_name,
            'input_mode': input_mode,
            'network': network,
            'bridge': bridge,
            'os_storage': storage,
            'os_pool': os_pool,
            'hostname': source_ip,
            'password': source_pwd,
            'v2v_opts': v2v_opts,
            'new_name': vm_name + utils_misc.generate_random_string(3),
            'output_method': output_method,
            'os_storage_name': storage_name,
            'rhv_upload_opts': rhv_upload_opts,
            'input_transport': input_transport,
            'vcenter_host': source_ip,
            'vcenter_password': source_pwd,
            'vddk_thumbprint': vddk_thumbprint,
            'vddk_libdir': vddk_libdir,
            'vddk_libdir_src': vddk_libdir_src,
            'params': params,
        }
        if vpx_dc:
            v2v_params.update({"vpx_dc": vpx_dc})
        if esx_ip:
            v2v_params.update({"esx_ip": esx_ip})
        output_format = params.get('output_format')
        if output_format:
            v2v_params.update({'of_format': output_format})
        # Build rhev related options
        if output_mode == 'rhev':
            # Create different sasl_user name for different job
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
        if output_mode == 'local':
            v2v_params['os_directory'] = data_dir.get_tmp_dir()
        if output_mode == 'libvirt':
            pvt.pre_pool(pool_name, pool_type, pool_target, '')
        # Set libguestfs environment variable
        os.environ['LIBGUESTFS_BACKEND'] = 'direct'

        # Save origin graphic type for result checking if source is KVM
        if hypervisor == 'kvm':
            ori_vm_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
            params['ori_graphic'] = ori_vm_xml.xmltreefile.find(
                'devices').find('graphics').get('type')
            params['vm_machine'] = ori_vm_xml.xmltreefile.find(
                './os/type').get('machine')

        backup_xml = None
        # Only kvm guest's xml needs to be backup currently
        if checkpoint in backup_list and hypervisor == 'kvm':
            backup_xml = ori_vm_xml
        if checkpoint == 'multi_disks':
            new_xml = vm_xml.VMXML.new_from_inactive_dumpxml(
                vm_name, virsh_instance=v2v_virsh)
            disk_count = len(new_xml.get_disk_all_by_expr('device==disk'))
            if disk_count <= 1:
                test.error('Not enough disk devices')
            params['ori_disks'] = disk_count
        if checkpoint == 'sata_disk':
            change_disk_bus('sata')
        if checkpoint.startswith('floppy'):
            if params['vm_machine'] and 'q35' in params['vm_machine'] and int(
                    re.search(r'pc-q35-rhel(\d+)\.', params['vm_machine']).group(1)) >= 8:
                test.cancel(
                    'Device isa-fdc is not supported with machine type %s' %
                    params['vm_machine'])
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
            params['mac_address'] = []
            vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(
                vm_name, virsh_instance=v2v_virsh)
            network_list = vmxml.get_iface_all()
            for mac in network_list:
                if network_list[mac].get('type') in ['bridge', 'network']:
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
                    # cirrus doesn't support 'ram' and 'vgamem' attribute
                    if video_type == 'cirrus':
                        [video.attrib.pop(attr_i) for attr_i in [
                            'ram', 'vgamem'] if attr_i in video.attrib]
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
            logging.info('Set service chronyd on')
            cmd = ['yum -y install chrony',
                   'systemctl start chronyd',
                   'chronyc add server %s' % ntp_server]
            vm_cmd(cmd)
        if checkpoint == 'sync_ntp':
            logging.info('Sync time with %s', ntp_server)
            cmd = ['yum -y install chrony',
                   'systemctl start chronyd',
                   'chronyc add server %s' % ntp_server,
                   'chronyc waitsync']
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
        if checkpoint == 'only_br':
            logging.info('Attatch bridge')
            virsh.attach_interface(
                vm_name, 'bridge virbr0 --current', debug=True)
        if checkpoint == 'no_libguestfs_backend':
            os.environ.pop('LIBGUESTFS_BACKEND')
        if checkpoint == 'file_image':
            vm = env.get_vm(vm_name)
            disk = vm.get_first_disk_devices()
            logging.info('Disk type is %s', disk['type'])
            if disk['type'] != 'file':
                test.error('Guest is not with file image')
        v2v_result = utils_v2v.v2v_cmd(v2v_params)
        if v2v_params.get('new_name'):
            vm_name = params['main_vm'] = v2v_params['new_name']
        check_result(v2v_result, status_error)
    finally:
        if close_virsh and v2v_virsh:
            logging.debug('virsh session %s is closing', v2v_virsh)
            v2v_virsh.close_session()
        if params.get('vmchecker'):
            params['vmchecker'].cleanup()
        if hypervisor == "xen":
            # Restore crypto-policies to DEFAULT, the setting is impossible to be
            # other values by default in testing envrionment.
            process.run(
                'update-crypto-policies --set DEFAULT',
                verbose=True,
                ignore_status=True,
                shell=True)
            utils_v2v.v2v_setup_ssh_key_cleanup(xen_session, xen_pubkey)
            process.run('ssh-agent -k')
        if output_mode == 'rhev' and v2v_sasl:
            v2v_sasl.cleanup()
            logging.debug('SASL session %s is closing', v2v_sasl)
            v2v_sasl.close_session()
        if output_mode == 'libvirt':
            pvt.cleanup_pool(pool_name, pool_type, pool_target, '')
        if backup_xml:
            backup_xml.sync()
        if params.get('selinux_stat') and params['selinux_stat'] != 'disabled':
            utils_selinux.set_status(params['selinux_stat'])
        if 'bk_firewalld_status' in params:
            service_mgr = service.ServiceManager()
            if service_mgr.status(
                    'firewalld') != params['bk_firewalld_status']:
                if params['bk_firewalld_status']:
                    service_mgr.start('firewalld')
                else:
                    service_mgr.stop('firewalld')
        if checkpoint.startswith('host_no_space'):
            large_file = os.path.join(data_dir.get_tmp_dir(), 'file.large')
            if os.path.isfile(large_file):
                os.remove(large_file)
        # Cleanup constant files
        utils_v2v.cleanup_constant_files(params)
