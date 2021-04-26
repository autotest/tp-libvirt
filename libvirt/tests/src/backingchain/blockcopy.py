import json
import logging
import os

from avocado.utils import process

from virttest import data_dir
from virttest import qemu_storage
from virttest import utils_misc
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Test vm backingchain, blockcopy
    """
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)
    case = params.get('case', '')

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    file_to_del = []
    tmp_dir = data_dir.get_data_dir()

    try:
        if case:
            if case == 'reuse_external':
                # Create a transient vm for test
                vm.undefine()
                virsh.create(vmxml.xml)

                all_disks = vmxml.get_disk_source(vm_name)
                if not all_disks:
                    test.error('Not found any disk file in vm.')
                image_file = all_disks[0].find('source').get('file')
                disk_dev = all_disks[0].find('target').get('dev')
                logging.debug('Image file of vm: %s', image_file)

                # Get image info
                image_info = utils_misc.get_image_info(image_file)
                logging.info('Image info: %s', image_info)

                # Get Virtual size of the image file
                vsize = image_info['vsize'] / 1073741824.0
                logging.info('Virtual size of image file: %f', vsize)

                new_image_size = vsize
                image_dir = '/'.join(image_file.split('/')[:-1])
                new_image_path = os.path.join(image_dir, 'new_image_' + utils_misc.generate_random_string(3))
                file_to_del.append(new_image_path)

                # Create new image file
                cmd_image_create = 'qemu-img create -f qcow2 %s %fG' % (new_image_path, new_image_size)
                process.run(cmd_image_create, shell=True, verbose=True)

                # Do blockcopy with --reuse-external option
                virsh.blockcopy(vm_name, disk_dev, new_image_path,
                                options='--verbose --wait --reuse-external',
                                debug=True, ignore_status=False)
                virsh.blockjob(vm_name, disk_dev, options='--pivot',
                               debug=True, ignore_status=False)
                logging.debug('Current vm xml: %s', vmxml)

                # Current disk source file should be new image
                cur_disks = vmxml.get_disk_source(vm_name)
                cur_sfile = cur_disks[0].find('source').get('file')
                logging.debug('Now disk source file is: %s', cur_sfile)
                if cur_sfile.strip() != new_image_path:
                    test.fail('Disk source file is not updated.')
            if case == 'custom_cluster_size':

                def update_vm_with_cluster_disk():
                    """
                    Update vm's first disk with a image which has customized
                    cluster size

                    :return: The source image params
                    """
                    source_img_params = params.copy()
                    source_img_params['image_name'] = params.get('source_image_name',
                                                                 'source_image')
                    source_img = qemu_storage.QemuImg(source_img_params, tmp_dir, '')
                    source_img_path, _ = source_img.create(source_img_params)
                    file_to_del.append(source_img_path)
                    source_img_params['disk_source_name'] = source_img_path
                    libvirt.set_vm_disk(vm, source_img_params)
                    return source_img_params

                source_img_params = update_vm_with_cluster_disk()
                all_disks = vmxml.get_disk_source(vm_name)
                if not all_disks:
                    test.error('Not found any disk file in vm.')
                disk_dev = all_disks[0].find('target').get('dev')

                # Blockcopy the source image to the target image path
                target_img_params = source_img_params.copy()
                target_img_name = params.get('target_image_name', 'target_image')
                target_img_params['image_name'] = target_img_name
                target_img_path = os.path.join(tmp_dir,
                                               target_img_name + '.qcow2')
                file_to_del.append(target_img_path)
                virsh.blockcopy(vm_name, disk_dev, target_img_path,
                                options='--verbose --wait --transient-job',
                                debug=True, ignore_status=False)
                target_img = qemu_storage.QemuImg(target_img_params, tmp_dir, '')
                target_img_info = json.loads(target_img.info(force_share=True,
                                                             output='json'))

                # Compare the source and target images' cluster size
                source_img_cluster = str(source_img_params.get('image_cluster_size'))
                target_img_cluster = str(target_img_info['cluster-size'])
                if source_img_cluster != target_img_cluster:
                    test.fail("Images have different cluster size:\n"
                              "Source image cluster size: %s\n"
                              "Target image cluster size: %s"
                              % (source_img_cluster, target_img_cluster))

                # Abort the blockcopy job
                virsh.blockjob(vm_name, disk_dev, options='--abort',
                               debug=True, ignore_status=False)

    finally:
        if case == 'reuse_external':
            # Recover vm and remove the transient vm
            virsh.destroy(vm_name, debug=True)
            virsh.define(bkxml.xml, debug=True)
        bkxml.sync()

        # Remove files to be deleted
        if file_to_del:
            for item in file_to_del:
                if os.path.exists(item):
                    os.remove(item)
