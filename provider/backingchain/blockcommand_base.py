import logging
import os
import re
import json

from avocado.utils import process

from virttest import utils_misc
from virttest import data_dir
from virttest import libvirt_storage
from virttest import virsh
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.disk import Disk
from virttest.utils_libvirt import libvirt_secret

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
        self.new_dev = self.params.get('target_disk', 'vda')
        self.src_path = ''
        self.snap_path_list = []
        self.snap_name_list = []
        self.tmp_dir = data_dir.get_data_dir()
        self.new_image_path = ''
        self.copy_image = ''
        self.old_parts = []
        self.original_disk_source = ''
        self.backing_file = ''
        self.lvm_list = []

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

    def prepare_snapshot(self, start_num=0, snap_num=3, snap_name="",
                         snap_path="", option='--disk-only', extra='',
                         clean_snap_file=True):
        """
        Prepare domain snapshot

        :params start_num: snap path start index
        :params snap_num: snapshot number, default value is 3
        :params snap_name: snapshot name
        :params snap_path: path of snap
        :params option: option to create snapshot, default value is '--disk-only'
        :params extra: extra option to create snap
        :params clean_snap_file: Clean snap file before create snap if True.
        """
        # Create backing chain
        for i in range(start_num, snap_num):
            if not snap_path:
                path = self.tmp_dir + '%d' % i
            else:
                path = snap_path
            if os.path.exists(path) and "reuse" not in option and clean_snap_file:
                libvirt.delete_local_disk('file', path)

            if not snap_name:
                name = 'snap%d' % i
            else:
                name = snap_name
            snap_option = "%s %s --diskspec %s,file=%s%s" % \
                          (name, option, self.new_dev, path, extra)

            virsh.snapshot_create_as(self.vm.name, snap_option,
                                     ignore_status=False,
                                     debug=True)
            self.snap_path_list.append(path)
            self.snap_name_list.append(name)

            if not utils_misc.wait_for(lambda: os.path.exists(path), 10, first=2):
                self.test.error("%s should be in snapshot list" % snap_name)

    def convert_expected_chain(self, expected_chain_index):
        """
        Convert expected chain from "4>1>base" to "[/*snap4, /*snap1, /base.image]"

        :param expected_chain_index: expected chain , such as "4>1>base"
        :return expected chain with list
        """
        expected_chain = []
        for i in expected_chain_index.split('>'):
            if i == "base":
                expected_chain.append(self.new_image_path)
            elif i == 'backing_file':
                expected_chain.append(self.backing_file)
            elif i == 'copy_file':
                expected_chain.append(self.copy_image)
            else:
                expected_chain.append(self.snap_path_list[int(i) - 1])
        LOG.debug("Expected chain is : %s", expected_chain)

        return expected_chain

    @staticmethod
    def get_relative_path(image_path):
        """
        Get image backing chain relative path

        :param image_path: image path


        Example:
            qemu-img info -U /var/lib/libvirt/images/d/d --backing-chain
        Get:
            image: /var/lib/libvirt/images/d/d
            file format: qcow2
            backing file: ../c/c (actual path: /var/lib/libvirt/images/d/../c/c)
            backing file format: qcow2
            Format specific information:
                compat: 1.1
                extended l2: false
            image: /var/lib/libvirt/images/d/../c/c
        The function returned:
            /var/lib/libvirt/images/d/../c/c
        """
        relative_path = []
        cmd = "qemu-img info %s --backing-chain" % image_path
        if libvirt_storage.check_qemu_image_lock_support():
            cmd += " -U"
        ret = process.run(cmd, shell=True).stdout_text.strip()
        match = re.findall(r'(image: )(.+\n)', ret)

        for i in range(len(match)):
            path = match[i][1].split('\n')[0]
            if 'json' in path:
                source = json.loads(path.split('json:')[1])["file"]
                path = source["pool"] + "/" + source["image"]
            relative_path.append(path)

        return relative_path

    def get_hash_value(self, session=None, check_item=''):
        """
        Get the hash value

        :param session: virsh session
        :param check_item: a file or device
        :return hash value
        """
        if session is None:
            session = self.vm.wait_for_login()

        status, _ = session.cmd_status_output("which sha256sum")
        if status:
            self.test.error("Not find sha256sum command on guest.")

        ret, expected_hash = session.cmd_status_output("sha256sum %s" % check_item)
        if ret:
            self.test.error("Get sha256sum value failed")

        return expected_hash

    def backingchain_common_teardown(self):
        """
        Clean all new created snap
        """
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

    def backingchain_common_setup(self, create_snap=False, **kwargs):
        """
        Setup step for backingchain test

        :param create_snap: create snap flag, will create if True.
        :param **kwargs: Key words for setup,
        """
        if not self.vm.is_alive():
            self.vm.start()
        self.vm.wait_for_login().close()

        if create_snap:
            self.prepare_snapshot(**kwargs)

        remove_file = kwargs.get("remove_file")
        if remove_file:
            self.clean_file(kwargs.get("file_path"))

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
        vmxml = vm_xml.VMXML.new_from_dumpxml(self.vm.name)
        self.test.log.debug("Current vm xml is :%s", vmxml)

    @staticmethod
    def clean_file(file_path, session=None):
        """
        Clean file

        :params file_path: the path of file to delete
        :params session: vm session
        """
        if session:
            session.cmd("rm -f %s" % file_path)
        elif os.path.exists(file_path):
            os.remove(file_path)
