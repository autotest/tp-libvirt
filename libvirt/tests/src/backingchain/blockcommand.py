import os
import logging
import re

from avocado.utils import process

from virttest import virsh
from virttest import data_dir
from virttest import gluster
from virttest import libvirt_storage
from virttest import utils_misc
from virttest.libvirt_xml import vm_xml
from virttest.staging import lv_utils
from virttest.staging import service
from virttest.libvirt_xml.devices.disk import Disk

from virttest.utils_test import libvirt


def check_backingchain(img_list, img_info):
    """
    Check backing chain info through qemu-img info

    :param img_list: expected backingchain list
    :param img_info: actual qemu-img info output
    :return: bool, meets expectation or not
    """
    pattern = ''
    chain_length = len(img_list)
    for i in range(chain_length):
        pattern += 'image: ' + img_list[i]
        if i + 1 < chain_length:
            pattern += '.*backing file: ' + img_list[i + 1]
        pattern += '.*'

    logging.debug(pattern)
    exist = re.search(pattern, img_info, re.DOTALL)
    logging.debug(exist)
    return exist


def check_bc_base_top(command, vmxml, dev, bc_chain):
    """
    Check backing chain info after blockpull/commit
    from base to top(or top to base)

    :param command: blockpull, blockcommit
    :param vmxml: vmxml obj of vm
    :param dev: dev of target disk
    :param bc_chain: original backing chain info as a list of source files
    :return: True if check passed, False if failed
    """
    logging.info('Check backing chain after %s', command)
    disk = vmxml.get_disk_all()[dev]
    disk_type = disk.get('type')
    disk_source = disk.find('source')
    if disk_type == 'file':
        src_path = disk_source.get('file')
    elif disk_type == 'block':
        src_path = disk_source.get('dev')
    elif disk_type == 'network':
        src_path = '{}://{}/{}'.format(disk_source.get('protocol'),
                                       disk_source.find('host').get('name'),
                                       disk_source.get('name'))
    else:
        logging.error('Unknown disk type: %s', disk_type)
        return False
    logging.debug('Current source file: %s', src_path)
    if command == 'blockpull':
        index = 0
    elif command == 'blockcommit':
        index = -1
    else:
        logging.error('Unsupported command: %s', command)
        return False
    if src_path != bc_chain[index]:
        logging.error('Expect source file to be %s, but got %s', bc_chain[index], src_path)
        return False
    bs = disk.find('backingStore')
    logging.debug('Current backing store: %s', bs)
    if bs and bs.find('source') is not None:
        logging.error('Backing store of disk %s, should be empty', dev)
        return False

    logging.info('Backing chain check PASS.')
    return True


def check_bc_middle_middle():
    pass


def run(test, params, env):
    """
    Test vm backingchain, blockcopy
    """
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)
    status_error = 'yes' == params.get('status_error', 'no')
    error_msg = params.get('error_msg', '')
    case = params.get('case', '')
    blockcommand = params.get('blockcommand', '')
    blk_top = int(params.get('top', 0))
    blk_base = int(params.get('base', 0))
    opts = params.get('opts', '--verbose --wait')
    check_func = params.get('check_func', '')
    disk_type = params.get('disk_type', '')
    disk_src = params.get('disk_src', '')
    driver_type = params.get('driver_type', 'qcow2')
    vol_name = params.get('vol_name', 'vol_blockpull')
    pool_name = params.get('pool_name', '')
    brick_path = os.path.join(data_dir.get_tmp_dir(), pool_name)
    vg_name = params.get('vg_name', 'HostVG')
    vol_size = params.get('vol_size', '10M')

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    # List to collect paths to delete after test
    file_to_del = []
    virsh_dargs = {'debug': True, 'ignore_status': False}

    try:
        all_disks = vmxml.get_disk_source(vm_name)
        if not all_disks:
            test.error('Not found any disk file in vm.')
        image_file = all_disks[0].find('source').get('file')
        logging.debug('Image file of vm: %s', image_file)

        # Get all dev of virtio disks to calculate the dev of new disk
        all_vdisks = [disk for disk in all_disks if disk.find('target').get('dev').startswith('vd')]
        disk_dev = all_vdisks[-1].find('target').get('dev')
        new_dev = disk_dev[:-1] + chr(ord(disk_dev[-1]) + 1)

        # Setup iscsi target
        if disk_src == 'iscsi':
            disk_target = libvirt.setup_or_cleanup_iscsi(
                is_setup=True, is_login=True,
                image_size='1G')
            logging.debug('ISCSI target: %s', disk_target)

        # Setup lvm
        elif disk_src == 'lvm':
            # Stop multipathd to avoid vgcreate fail
            multipathd = service.Factory.create_service("multipathd")
            multipathd_status = multipathd.status()
            if multipathd_status:
                multipathd.stop()

            # Setup iscsi target
            device_name = libvirt.setup_or_cleanup_iscsi(
                is_setup=True, is_login=True,
                image_size='1G')
            logging.debug('ISCSI target for lvm: %s', device_name)

            # Create logical device
            logical_device = device_name
            lv_utils.vg_create(vg_name, logical_device)
            vg_created = True

            # Create logical volume as backing store
            vol_bk, vol_disk = 'vol1', 'vol2'
            lv_utils.lv_create(vg_name, vol_bk, vol_size)

            disk_target = '/dev/%s/%s' % (vg_name, vol_bk)
            src_vol = '/dev/%s/%s' % (vg_name, vol_disk)

        # Setup gluster
        elif disk_src == 'gluster':
            host_ip = gluster.setup_or_cleanup_gluster(
                is_setup=True, vol_name=vol_name,
                brick_path=brick_path, pool_name=pool_name
            )
            logging.debug(host_ip)
            gluster_img = 'test.img'
            img_create_cmd = "qemu-img create -f raw /mnt/%s 10M" % gluster_img
            process.run("mount -t glusterfs %s:%s /mnt; %s; umount /mnt"
                        % (host_ip, vol_name, img_create_cmd), shell=True)
            disk_target = 'gluster://%s/%s/%s' % (host_ip, vol_name, gluster_img)

        else:
            test.error('Wrong disk source, unsupported by this test.')

        new_image = os.path.join(os.path.split(image_file)[0], 'test.img')
        params['snapshot_list'] = ['s%d' % i for i in range(1, 5)]

        if disk_src == 'lvm':
            new_image = src_vol
            if disk_type == 'block':
                new_image = disk_target
                for i in range(2, 6):
                    lv_utils.lv_create(vg_name, 'vol%s' % i, vol_size)
                snapshot_image_list = ['/dev/%s/vol%s' % (vg_name, i) for i in range(2, 6)]
        else:
            file_to_del.append(new_image)
            snapshot_image_list = [new_image.replace('img', i) for i in params['snapshot_list']]

        cmd_create_img = 'qemu-img create -f %s -b %s %s' % (driver_type, disk_target, new_image)
        if disk_type == 'block' and driver_type == 'raw':
            pass
        else:
            process.run(cmd_create_img, verbose=True, shell=True)
        info_new = utils_misc.get_image_info(new_image)
        logging.debug(info_new)

        # Create xml of new disk and add it to vmxml
        if disk_type:
            new_disk = Disk()
            new_disk.xml = libvirt.create_disk_xml({
                'type_name': disk_type,
                'driver_type': driver_type,
                'target_dev': new_dev,
                'source_file': new_image
            })

            logging.debug(new_disk.xml)

            vmxml.devices = vmxml.devices.append(new_disk)
            vmxml.xmltreefile.write()
            logging.debug(vmxml)
            vmxml.sync()

        vm.start()
        logging.debug(virsh.dumpxml(vm_name))

        # Create backing chain
        for i in range(len(params['snapshot_list'])):
            virsh.snapshot_create_as(
                vm_name,
                '%s --disk-only --diskspec %s,file=%s,stype=%s' %
                (params['snapshot_list'][i], new_dev, snapshot_image_list[i],
                 disk_type),
                **virsh_dargs
            )

            # Get path of each snapshot file
            snaps = virsh.domblklist(vm_name, debug=True).stdout.splitlines()
            for line in snaps:
                if line.lstrip().startswith(('hd', 'sd', 'vd')):
                    file_to_del.append(line.split()[-1])

        qemu_img_cmd = 'qemu-img info --backing-chain %s' % snapshot_image_list[-1]
        if libvirt_storage.check_qemu_image_lock_support():
            qemu_img_cmd += " -U"
        bc_info = process.run(qemu_img_cmd, verbose=True, shell=True).stdout_text

        if not disk_type == 'block':
            bc_chain = snapshot_image_list[::-1] + [new_image, disk_target]
        else:
            bc_chain = snapshot_image_list[::-1] + [new_image]
        bc_result = check_backingchain(bc_chain, bc_info)
        if not bc_result:
            test.fail('qemu-img info output of backing chain is not correct: %s'
                      % bc_info)

        # Generate blockpull/blockcommit options
        virsh_blk_cmd = eval('virsh.%s' % blockcommand)
        if blockcommand == 'blockpull' and blk_base != 0:
            opts += '--base {dev}[{}]'.format(blk_base, dev=new_dev)
        elif blockcommand == 'blockcommit':
            opt_top = ' --top {dev}[{}]'.format(blk_top, dev=new_dev) if blk_top != 0 else ''
            opt_base = ' --base {dev}[{}]'.format(blk_base, dev=new_dev) if blk_base != 0 else ''
            opts += opt_top + opt_base + ' --active' if blk_top == 0 else ''

        # Do blockpull/blockcommit
        virsh_blk_cmd(vm_name, new_dev, opts, **virsh_dargs)
        if blockcommand == 'blockcommit':
            virsh.blockjob(vm_name, new_dev, '--pivot', **virsh_dargs)
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        logging.debug("XML after %s: %s" % (blockcommand, vmxml))

        # Check backing chain after blockpull/blockcommit
        check_bc_func_name = 'check_bc_%s' % check_func
        if check_bc_func_name in globals():
            check_bc = eval(check_bc_func_name)
            if not callable(check_bc):
                logging.warning('Function "%s" is not callable.', check_bc_func_name)
            if not check_bc(blockcommand, vmxml, new_dev, bc_chain):
                test.fail('Backing chain check after %s failed' % blockcommand)
        else:
            logging.warning('Function "%s" is not implemented.', check_bc_func_name)

        virsh.dumpxml(vm_name, debug=True)

        # Check whether login is successful
        try:
            vm.wait_for_login().close()
        except Exception as e:
            test.fail('Vm login failed')

    finally:
        logging.info('Start cleaning up.')
        for ss in params.get('snapshot_list', []):
            virsh.snapshot_delete(vm_name, '%s --metadata' % ss, debug=True)
        bkxml.sync()
        for path in file_to_del:
            logging.debug('Remove %s', path)
            if os.path.exists(path):
                os.remove(path)
        if disk_src == 'iscsi':
            libvirt.setup_or_cleanup_iscsi(is_setup=False)
        elif disk_src == 'lvm':
            process.run('rm -rf /dev/%s/%s' % (vg_name, vol_disk), ignore_status=True)
            if 'vol_bk' in locals():
                lv_utils.lv_remove(vg_name, vol_bk)
            if 'vg_created' in locals() and vg_created:
                lv_utils.vg_remove(vg_name)
                cmd = "pvs |grep %s |awk '{print $1}'" % vg_name
                pv_name = process.system_output(cmd, shell=True, verbose=True).strip()
                if pv_name:
                    process.run("pvremove %s" % pv_name, verbose=True, ignore_status=True)
            libvirt.setup_or_cleanup_iscsi(is_setup=False)
        elif disk_src == 'gluster':
            gluster.setup_or_cleanup_gluster(is_setup=False, vol_name=vol_name, brick_path=brick_path, pool_name=pool_name)
        if 'multipathd_status' in locals() and multipathd_status:
            multipathd.start()
