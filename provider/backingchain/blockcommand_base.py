import logging

from virttest import virsh
from virttest import data_dir
from virttest.utils_test import libvirt

LOG = logging.getLogger('avocado.backingchain.blockcommand_base.')


class BlockCommand(object):
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
        self.new_dev = ''
        self.src_path = ''
        self.snap_path_list = []
        self.snap_name_list = []
        self.tmp_dir = data_dir.get_data_dir()
        self.new_image_path = ''

    def prepare_iscsi(self):
        """
        Prepare iscsi target and set specific params for disk.
        """
        driver_type = self.params.get('driver_type', 'raw')
        disk_type = self.params.get('disk_type', 'block')

        first_disk = self.vm.get_first_disk_devices()
        new_dev = first_disk['target']

        # Setup iscsi target
        device_name = libvirt.setup_or_cleanup_iscsi(is_setup=True)
        # Prepare block type disk params
        self.params['cleanup_disks'] = 'yes'
        self.params.update({'disk_format': driver_type,
                            'disk_type': disk_type,
                            'disk_target': new_dev,
                            'disk_source_name': device_name})
        self.new_dev = new_dev
        self.src_path = device_name

    def update_disk(self):
        """
        Update vm disk with specific params

        """
        # Get disk type
        disk_type = self.params.get('disk_type')

        # Check disk type
        if disk_type == 'block':
            if not self.vm.is_alive():
                self.vm.start()
            self.prepare_iscsi()

        # Update vm disk
        libvirt.set_vm_disk(self.vm, self.params)

    def prepare_snapshot(self, snap_num=3, option='--disk-only'):
        """
        Prepare domain snapshot

        :params snap_num: snapshot number, default value is 3
        :params option: option to create snapshot, default value is '--disk-only'
        """
        # Create backing chain
        for i in range(snap_num):
            snap_option = "%s %s --diskspec %s,file=%s" % \
                          ('snap%d' % i, option, self.new_dev,
                           self.tmp_dir + 'snap%d' % i)

            virsh.snapshot_create_as(self.vm.name, snap_option,
                                     ignore_status=False,
                                     debug=True)
            self.snap_path_list.append(self.tmp_dir + 'snap%d' % i)
            self.snap_name_list.append('snap%d' % i)
