import logging
import os

from avocado.utils import process

from virttest import virsh, utils_disk
from virttest import data_dir
from virttest.utils_libvirt import libvirt_secret
from virttest.libvirt_xml.devices.disk import Disk
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt

LOG = logging.getLogger('avocado.' + __name__)


class BlockCommand(object):
    """
        Prepare data for blockcommand test

        :param test: Test object
        :param vm: A libvirt_vm.VM class instance.
        :param params: Dict with the test parameters.
    """
    def __init__(self, test, vm, params):
        self.test = test
        self.vm = vm
        self.params = params
        self.new_dev = "vda"
        self.src_path = ''
        self.snap_path_list = []
        self.snap_name_list = []
        self.tmp_dir = data_dir.get_data_dir()
        self.new_image_path = ''
        self.old_parts = []
        self.original_disk_source = ''
        self.dd_file_list = []

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

    def update_disk(self, disk_type, disk_dict):
        """
        Update vm disk with specific params

        :param disk_type: disk type
        :param disk_dict: dict to update disk
        """
        vmxml = vm_xml.VMXML.new_from_dumpxml(self.vm.name)

        if disk_type == 'file':
            return
        elif disk_type == 'block':
            if not self.vm.is_alive():
                self.vm.start()
            device_name = libvirt.setup_or_cleanup_iscsi(is_setup=True)
            disk_dict.update({'source': {'attrs': {'dev': device_name}}})

        elif disk_type == 'volume':
            pass
        elif disk_type == 'NFS':
            pass
        elif disk_type == 'rbd_with_auth':
            pass

        new_disk = Disk()
        new_disk.setup_attrs(**disk_dict)
        vmxml.devices = new_disk
        vmxml.xmltreefile.write()
        vmxml.sync()

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

            session = self.vm.wait_for_login()
            utils_disk.dd_data_to_vm_disk(session, '/mnt/s%s.img' % i)
            session.close()
            self.dd_file_list.append('/mnt/s%s.img' % i)

    def backingchain_common_teardown(self):
        """
        Clean all new created snap
        """
        LOG.info('Start cleaning up.')
        for ss in self.snap_name_list:
            virsh.snapshot_delete(self.vm.name, '%s --metadata' % ss, debug=True)
        for sp in self.snap_path_list:
            process.run('rm -f %s' % sp)
        # clean left first disk snap file that created along with new disk
        image_path = os.path.dirname(self.original_disk_source)
        if image_path != '':
            for sf in os.listdir(image_path):
                if 'snap' in sf:
                    process.run('rm -f %s/%s' % (image_path, sf))

    def prepare_secret_disk(self, image_path, secret_disk_dict=None):
        """
        Add secret disk for domain.

        :params image_path: image path for disk source file
        :secret_disk_dict: secret disk dict to add new disk
        """
        vmxml = vm_xml.VMXML.new_from_dumpxml(self.vm.name)
        sec_dict = eval(self.params.get("sec_dict", '{}'))
        device_target = self.params.get("target_disk", "vdd")
        sec_passwd = self.params.get("private_key_password")
        if not secret_disk_dict:
            secret_disk_dict = {'type_name': "file",
                                'target': {"dev": device_target, "bus": "virtio"},
                                'driver': {"name": "qemu", "type": "qcow2"},
                                'source': {'encryption': {"encryption": 'luks',
                                                          "secret": {"type": "passphrase"}}}}
        # Create secret
        libvirt_secret.clean_up_secrets()
        sec_uuid = libvirt_secret.create_secret(sec_dict=sec_dict)
        virsh.secret_set_value(sec_uuid, sec_passwd, encode=True,
                               debug=True)

        secret_disk_dict['source']['encryption']['secret']["uuid"] = sec_uuid
        secret_disk_dict['source']['attrs'] = {'file': image_path}
        new_disk = Disk()
        new_disk.setup_attrs(**secret_disk_dict)
        vmxml.devices = vmxml.devices.append(new_disk)
        vmxml.xmltreefile.write()
        vmxml.sync()
