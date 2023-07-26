import logging
import os
import shutil

from virttest import utils_misc
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.save import save_base

LOG = logging.getLogger('avocado.test.' + __name__)
VIRSH_ARGS = {'debug': True, 'ignore_status': False}


def setup_nfs_file(vmxml, image_name, disk_seclabels, uid, gid):
    """
    setup nfs storage for vm disk image and as location to save to

    :param vmxml: vmxml instance
    :param image_name: image name of vm disk
    :param disk_seclabels: seclabel of disk
    :param uid: uid to set to image
    :param gid: gid to set to image
    :return: path to save vm to
    """
    nfs = libvirt.setup_or_cleanup_nfs(is_setup=True, is_mount=True)
    disks = vmxml.get_devices('disk')
    disk_source_file = disks[0].source.attrs['file']
    disk_source_nfs = os.path.join(nfs['mount_dir'], image_name)
    rand_id = utils_misc.generate_random_string(3)
    save_path = os.path.join(nfs['mount_dir'],
                             f'{vmxml.vm_name}_{rand_id}.save')
    shutil.copyfile(disk_source_file, disk_source_nfs)
    os.chown(disk_source_nfs, uid, gid)
    source_attrs = {'attrs': {**disks[0].source.attrs,
                              **{'file': disk_source_nfs}},
                    **disk_seclabels}
    libvirt_vmxml.modify_vm_device(vmxml, 'disk', {'source': source_attrs})

    return save_path


def run(test, params, env):
    """
    Test virsh restore from file on nfs storage
    """
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)

    disk_seclabels = eval(params.get('disk_seclabels', '{}'))
    image_name = f'{vm_name}.img'
    uid = int(params.get('uid'))
    gid = int(params.get('gid'))

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    try:
        save_path = setup_nfs_file(vmxml, image_name, disk_seclabels, uid, gid)
        LOG.debug(f'VM xml: {virsh.dumpxml(vm_name).stdout_text}')

        vm.start()
        vm.wait_for_login().close()
        virsh.save(vm_name, save_path, **VIRSH_ARGS)
        os.chown(save_path, uid, gid)

        virsh.restore(save_path, **VIRSH_ARGS)

        LOG.debug(f'VM state after restore: {vm.state()}')
        if vm.state() != 'running':
            test.fail(f'VM should be running after restore, not {vm.state()}')
        save_base.check_ownership(save_path, uid, gid)

        virsh.shutdown(vm_name, **VIRSH_ARGS)
        save_base.check_ownership(save_path, uid, gid)

    finally:
        bkxml.sync()
        if os.path.exists(save_path):
            os.remove(save_path)
        libvirt.setup_or_cleanup_nfs(is_setup=False)
