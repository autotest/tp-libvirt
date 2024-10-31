import logging
import os
import pwd

from virttest import utils_config
from virttest import utils_disk
from virttest import utils_libvirtd
from virttest import utils_misc
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.save import save_base
from provider.security import security_base

LOG = logging.getLogger('avocado.test.' + __name__)
VIRSH_ARGS = {'debug': True, 'ignore_status': False}


def run(test, params, env):
    """
    Test save vm to nfs storage
    """
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)
    uid = int(params.get('uid'))
    gid = int(params.get('gid'))
    mod = int(params.get('mod'))
    export_opt = params.get('export_opt')
    nfs_mount_dir = params.get('nfs_mount_dir')
    chown_img = params.get('chown_img')
    rand_id = utils_misc.generate_random_string(3)

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    bk_disks_label = {}

    qemu_conf = utils_config.LibvirtQemuConfig()
    libvirtd = utils_libvirtd.Libvirtd()

    try:
        # Workaround bug: Remove multi-queue setting
        libvirt_vmxml.modify_vm_device(vmxml, 'interface', {'driver': None})

        qemu_conf.dynamic_ownership = 0
        if vmxml.devices.by_device_tag('tpm') is not None:
            qemu_conf.swtpm_user = 'qemu'
            qemu_conf.swtpm_group = 'qemu'
            security_base.set_tpm_perms(vmxml, params)
        libvirtd.restart()

        if chown_img:
            qemu_info = pwd.getpwnam(chown_img.split(':')[0])
            uid, gid = qemu_info.pw_uid, qemu_info.pw_gid
            for disk in list(vm.get_disk_devices().values()):
                disk_path = disk['source']
                stat_re = os.lstat(disk_path)
                bk_disks_label[disk_path] = f'{stat_re.st_uid}:{stat_re.st_gid}'
                LOG.debug(f'Update image ownership to {uid}:{gid}')
                os.chown(disk_path, uid, gid)

        nfs = libvirt.setup_or_cleanup_nfs(is_setup=True, is_mount=False,
                                           export_options=export_opt)
        save_file_name = f'{vmxml.vm_name}_{rand_id}.save'
        save_path = os.path.join(nfs['export_dir'], save_file_name)
        LOG.debug(f'Create empty test file in export dir: {save_path}')
        open(save_path, 'w').close()
        LOG.debug(f'Update test file ownership to {uid}:{gid}')
        os.chown(save_path, uid, gid)
        LOG.debug(f'Update test file mod to {mod}')
        os.chmod(save_path, mod)

        LOG.debug('Make nfs mount dir and mount nfs storage')
        if not os.path.exists(nfs_mount_dir):
            os.mkdir(nfs_mount_dir)
        utils_disk.mount('127.0.0.1:' + nfs['export_dir'], nfs_mount_dir,
                         'nfs', options='rw')

        save_path = os.path.join(nfs_mount_dir, save_file_name)
        LOG.debug(f'Target save path after nfs mount: {save_path}')
        save_base.check_ownership(save_path, uid, gid)

        vm.start()
        pid_ping, upsince = save_base.pre_save_setup(vm)
        virsh.save(vm_name, save_path, **VIRSH_ARGS)

        save_base.check_ownership(save_path, uid, gid)
        virsh.restore(save_path, **VIRSH_ARGS)

        LOG.debug(f'VM state after restore: {vm.state()}')
        if vm.state() != 'running':
            test.fail(f'VM should be running after restore, not {vm.state()}')
        save_base.check_ownership(save_path, uid, gid)
        save_base.post_save_check(vm, pid_ping, upsince)

        virsh.shutdown(vm_name, **VIRSH_ARGS)
        save_base.check_ownership(save_path, uid, gid)

    finally:
        qemu_conf.restore()
        for path, label in list(bk_disks_label.items()):
            label_list = label.split(':')
            os.chown(path, int(label_list[0]), int(label_list[1]))
        security_base.restore_tpm_perms(vmxml, params)
        bkxml.sync()
        utils_disk.umount(nfs['export_dir'], nfs_mount_dir)
        os.rmdir(nfs_mount_dir)
        if os.path.exists(save_path):
            os.remove(save_path)
        libvirt.setup_or_cleanup_nfs(is_setup=False)
        libvirtd.restart()
