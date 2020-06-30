import os
import logging

from avocado.utils import process

from virttest import virsh
from virttest import utils_misc
from virttest.libvirt_xml import vm_xml


def run(test, params, env):
    """
    Test vm backingchain, blockcopy
    """
    vm_name = params.get('main_vm')
    case = params.get('case', '')

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    file_to_del = []

    try:
        if case:
            if case == 'reuse_external':
                # Create a transient vm for test
                virsh.undefine(vm_name, debug=True, ignore_status=False)
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
