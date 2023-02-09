import logging
import re

from avocado.utils import process

from virttest import libvirt_storage
from virttest import utils_misc
from virttest.libvirt_xml import vm_xml
from provider.virtual_disk.disk_base import DiskBase

LOG = logging.getLogger('avocado.' + __name__)


class Checkfunction(object):
    """
    Prepare data for blockcommand test

    :param test: Test object
    :param vm:  A libvirt_vm.VM class instance.
    :param params: Dict with the test parameters.
    """

    def __init__(self, test, vm, params):
        self.test = test
        self.vm = vm
        self.params = params
        self.original_disk_source = ''

    def check_backingchain_from_vmxml(self, disk_type, target_dev, expected_chain):
        """
        Compare vmxml backingchain and expected chain

        :param disk_type: disk type
        :param target_dev: dev of target disk
        :param expected_chain: expected backingchain
        """
        vmxml = vm_xml.VMXML.new_from_dumpxml(self.vm.name)
        LOG.debug("Current vmxml is:\n%s", vmxml)
        source_list = DiskBase.get_source_list(vmxml, disk_type, target_dev)

        if source_list != expected_chain:
            self.test.fail('Expect source file to be %s, '
                           'but got %s' % (expected_chain, source_list))
        else:
            LOG.debug('Get correct backingchin')

    def check_block_operation_result(self, vmxml, blockcommand,
                                     target_dev, bc_chain):
        """
        Run specific check backing chain function.

        :param vmxml: vmxml obj of vm
        :param blockcommand: blockpull, blockcommit..
        :param target_dev: dev of target disk, different disks could have
        different chain
        :param bc_chain: original backing chain info as a list of source files
        """
        check_func = self.params.get('check_func', '')
        check_bc_func_name = 'self.check_bc_%s' % check_func
        check_bc = eval(check_bc_func_name)
        # Run specific check backing chain function
        if not check_bc(blockcommand, vmxml, target_dev, bc_chain):
            self.test.fail('Backing chain check after %s failed' % blockcommand)

    def _get_image_size_with_bytes(self, expected_value):
        """
        Handle the image size that setting in cfg file to bytes unit.

        :param expected_value: image size that setting in cfg file.
        """
        expected_number = int(re.findall(r'\d+', expected_value)[0])
        expected_unit = re.findall(r"\D+", expected_value)[0]

        if expected_unit == "kib":
            expected_value = expected_number * 1024
        elif expected_unit in ["b", "k", "m", "g"]:
            expected_value = expected_number * 1024 ** ["b", "k", "m", "g"]. \
                index(expected_unit)
        else:
            self.test.error("Unknown scale value:%s", expected_unit)
        return int(expected_value)

    def check_image_info(self, image_path, check_item, expected_value):
        """
        Check value is expected in image info

        :param image_path: image path
        :param check_item: The item you want to check.
        :param expected_value: expected item value
        """
        image_info = utils_misc.get_image_info(image_path)

        if image_info.get(check_item) is None:
            self.test.fail("The {} value:{} you checked is"
                           " not returned in image_info:{}".
                           format(check_item, expected_value, image_info))
        else:
            actual_value = image_info[check_item]
            # Get actual value
            if check_item == 'vsize':
                expected_value = self._get_image_size_with_bytes(expected_value)
            # check item value
            if actual_value != expected_value:
                self.test.fail('The value :{} is not expected value:'
                               '{}'.format(actual_value, expected_value))

    def check_bc_base_top(self, command, vmxml, dev, bc_chain):
        """
        Check backing chain info after blockpull/commit
        from base to top(or top to base)

        :param command: blockpull, blockcommit..
        :param vmxml: vmxml obj of vm
        :param dev: dev of target disk
        :param bc_chain: original backing chain info as a list of source files
        :return: True if check passed, False if failed
        """
        LOG.info('Check backing chain after %s', command)
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
            LOG.error('Unknown disk type: %s', disk_type)
            return False
        LOG.debug('Current source file: %s', src_path)
        if command == 'blockpull':
            index = 0
        elif command == 'blockcommit':
            index = -1
        else:
            LOG.error('Unsupported command: %s', command)
            return False
        if src_path != bc_chain[index]:
            LOG.error('Expect source file to be %s, '
                      'but got %s' % (bc_chain[index], src_path))
            return False
        bs = disk.find('backingStore')
        LOG.debug('Current backing store: %s', bs)
        if bs and bs.find('source') is not None:
            LOG.error('Backing store of disk %s, should be empty', dev)
            return False

        LOG.info('Backing chain check PASS.')
        return True

    def check_backingchain(self, img_list):
        """
        Check backing chain info through qemu-img info

        :param img_list: expected backingchain list
        :return: bool, meets expectation or not
        """
        # Get actual backingchain list
        qemu_img_cmd = 'qemu-img info --backing-chain %s' % img_list[0]
        if libvirt_storage.check_qemu_image_lock_support():
            qemu_img_cmd += " -U"
        img_info = process.run(qemu_img_cmd, verbose=True, shell=True).stdout_text
        # Set check pattern
        pattern = ''
        chain_length = len(img_list)
        for i in range(chain_length):
            pattern += 'image: ' + img_list[i]
            if i + 1 < chain_length:
                pattern += '.*backing file: ' + img_list[i + 1]
            pattern += '.*'
        LOG.debug('The pattern to match the current backing chain is:%s'
                  % pattern)
        # compare list
        exist = re.search(pattern, img_info, re.DOTALL)
        if not exist:
            self.test.fail('qemu-img info output of backing chain '
                           'is not correct: %s' % img_info)
        return exist

    def check_hash_list(self, item_list, hash_list, session=None):
        """
        Check the file list current hash value same as the hash value list

        :param item_list: file or dev need to check
        :param hash_list: hash value list
        :param session: The session object to the host
        """

        for index, item in enumerate(item_list):
            if session:
                ret, current_hash = session.cmd_status_output("sha256sum %s" % item)
            else:
                current_hash = process.run("sha256sum %s" % item).stdout_text.strip().split()[0]

            if current_hash != hash_list[index]:
                self.test.fail("File:%s hash :%s is different from hash before "
                               "blockcommit:%s" % (item, current_hash,
                                                   item_list[index]))

    def check_mirror_exist(self, vm, device, image_path):
        """
        Check mirror tag exist and correct source file

        :param vm: the vm object
        :param device: device name
        :param image_path: image path
        """
        # Check exist mirror tag.
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm.name)
        disk_list = vmxml.get_disk_all()[device]
        if not disk_list.find('mirror'):
            self.test.fail('No mirror tag in current domain xml :%s' % vmxml)
        else:
            # Check file in mirror should be new path
            mirror_file = disk_list.find('mirror').get('file')
            if mirror_file != image_path:
                self.test.fail('Current mirror tag file:%s is not same as:%s' %
                               (mirror_file, image_path))
            # Check file in mirror >source > file should be new path
            mirror_source_file = disk_list.find('mirror'). \
                find('source').get('file')
            if mirror_source_file != image_path:
                self.test.fail('Current source tag file:%s is not same as:%s' %
                               (mirror_source_file, image_path))
