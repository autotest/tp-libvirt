import logging

from avocado.utils import lv_utils, process

from virttest import ceph
from virttest import data_dir
from virttest import virsh
from virttest.libvirt_xml import pool_xml
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.disk import Disk
from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_disk
from virttest.utils_libvirt import libvirt_secret

LOG = logging.getLogger('avocado.' + __name__)


class DiskBase(object):
    """
    Prepare disk related function
    """

    def __init__(self, test, vm, params):
        """
        Init the default value for disk object.

        :param test: Test object
        :param vm: A libvirt_vm.VM class instance.
        :param params: Dict with the test parameters.
        """
        self.test = test
        self.vm = vm
        self.params = params
        self.vg_name = 'vg0'
        self.lv_name = 'lv0'
        self.lv_num = 4
        self.pool_name = params.get("pool_name", 'pool_name')
        self.pool_type = params.get("pool_type", 'dir')
        self.pool_target = params.get("pool_target", 'dir_pool')
        self.emulated_image = params.get("emulated_image", 'dir_pool_img')
        self.base_dir = params.get("base_dir", "/var/lib/libvirt/images")
        self.new_image_path = ''
        self.path_list = ''

    @staticmethod
    def get_source_list(vmxml, disk_type, target_dev):
        """
        Get vmxml all source list with specific device, include backingstore
        and source path

        :param vmxml: vm xml
        :param disk_type: disk type
        :param target_dev: dev of target disk
        :return source list, include backingstore and source path
        """
        source_list = []
        disks = vmxml.devices.by_device_tag('disk')
        for disk in disks:
            if disk.target['dev'] == target_dev:
                active_level_path = \
                    disk.xmltreefile.find('source').get('file') \
                    or disk.xmltreefile.find('source').get('dev') \
                    or disk.xmltreefile.find('source').get('volume') \
                    or disk.xmltreefile.find('source').get('name')

                backing_list = disk.get_backingstore_list()
                if disk_type != 'rbd_with_auth':
                    backing_list.pop()
                source_list = [elem.find('source').get('file') or
                               elem.find('source').get('name') or
                               elem.find('source').get('dev') or
                               elem.find('source').get('volume')
                               for elem in backing_list
                               if elem.find("source") is not None]
                source_list.insert(0, active_level_path)
                break

        return source_list

    def add_vm_disk(self, disk_type, disk_dict, new_image_path='', **kwargs):
        """
        Add vm a disk.

        :param disk_type: disk type
        :param disk_dict: dict to update disk
        :param new_image_path: new disk image if needed
        :return new_image_path: return the updated new image path of new disk
        """

        vmxml = vm_xml.VMXML.new_from_dumpxml(self.vm.name)

        if disk_type == 'file':
            if not new_image_path:
                new_image_path = data_dir.get_data_dir() + '/test.img'
                size = kwargs.get("size", "50M")
                if kwargs.get("size"):
                    kwargs.pop("size")
                libvirt.create_local_disk("file", path=new_image_path,
                                          size=size, disk_format="qcow2", **kwargs)
            disk_dict.update({'source': {'attrs': {'file': new_image_path}}})

        elif disk_type == 'block':
            if not new_image_path:
                new_image_path = self.create_lvm_disk_path(
                    vg_name=self.vg_name, lv_name=self.lv_name, **kwargs)
            disk_dict.update({'source': {'attrs': {'dev': new_image_path}}})

        elif disk_type == 'volume':
            if not new_image_path:
                pool_name, new_image_path = self.create_volume_for_disk_path(
                    self.test, self.params, self.pool_name,
                    self.pool_type, self.pool_target, self.emulated_image, **kwargs)
            disk_dict.update({'source': {'attrs': {'pool': self.pool_name,
                                                   'volume': new_image_path}}})
        elif disk_type == 'nfs':
            if not new_image_path:
                nfs_res = libvirt.setup_or_cleanup_nfs(is_setup=True, is_mount=True)
                new_image_path = nfs_res['mount_dir'] + '/test.img'

                libvirt.create_local_disk("file", path=new_image_path, size='50M',
                                          disk_format="qcow2", **kwargs)
            disk_dict.update({'source': {'attrs': {'file': new_image_path}}})

        elif disk_type == 'rbd_with_auth':
            no_secret = kwargs.get("no_secret")
            if no_secret:
                disk_dict.update({'source': {'attrs': {'file': new_image_path}}})
            else:
                mon_host, auth_username, new_image_path = \
                    self.create_rbd_disk_path(self.params)
                disk_dict.update({'source': {
                        'attrs': {'protocol': "rbd", "name": new_image_path},
                        "hosts": [{"name": mon_host}],
                        "auth": {"auth_user": auth_username,
                                 "secret_usage": "cephlibvirt",
                                 "secret_type": "ceph"}}})

        disk_xml = self.set_disk_xml(disk_dict)
        libvirt.add_vm_device(vmxml, disk_xml)

        vmxml = vm_xml.VMXML.new_from_dumpxml(self.vm.name)
        LOG.debug('vmxml after update disk is:%s', vmxml)

        return new_image_path

    def cleanup_disk_preparation(self, disk_type):
        """
        Clean up the preparation of different type disk

        :param disk_type: disk type
        """
        if disk_type == 'block':
            self.cleanup_block_disk_preparation(self.vg_name, self.lv_name)

        elif disk_type == 'file':
            process.run('rm -f %s' % self.new_image_path)

        elif disk_type == 'volume':
            pvt = libvirt.PoolVolumeTest(self.test, self.params)
            pvt.cleanup_pool(self.pool_name, self.pool_type, self.pool_target,
                             self.emulated_image)

        elif disk_type == 'nfs':
            libvirt.setup_or_cleanup_nfs(is_setup=False)

        elif disk_type == "rbd_with_auth":
            libvirt_secret.clean_up_secrets()

    @staticmethod
    def cleanup_block_disk_preparation(vg_name, lv_name):
        """
        Clean up volume group, logical volume, iscsi target.

        :params vg_name: volume group name
        :params lv_name: volume name
        """
        lv_utils.lv_remove(vg_name, lv_name)
        lv_utils.vg_remove(vg_name)
        libvirt.setup_or_cleanup_iscsi(is_setup=False)

    @staticmethod
    def create_volume_for_disk_path(test, params, pool_name='pool_name',
                                    pool_type='dir', pool_target='dir_pool',
                                    emulated_image='dir_pool_img',
                                    vol_name='vol_name', capacity='2G',
                                    allocation='2G', volume_type='qcow2', **kwargs):
        """
        Prepare volume type disk image path

        :param test: test object
        :param params: Dict with the test parameters.
        :param pool_name: pool name
        :param vol_name: volume name
        :param capacity: volume capacity size
        :param allocation: volume allocation size
        :param pool_target: target of storage pool
        :param pool_type: dir, disk, logical, fs, netfs or else
        :param emulated_image: use an image file to simulate a scsi disk, it could
        be used for disk, logical pool, etc
        :param volume_type: volume type
        :return pool_name, vol_name: For disk source path
        """
        pvt = libvirt.PoolVolumeTest(test, params)
        pvt.pre_pool(pool_name, pool_type, pool_target, emulated_image)

        virsh.vol_create_as(vol_name, pool_name, capacity, allocation,
                            volume_type, debug=True, **kwargs)

        return pool_name, vol_name

    @staticmethod
    def create_lvm_disk_path(vg_name='vg0', lv_name='lv0', **kwargs):
        """
        Prepare lvm type disk image path

        :params vg_name: volume group name
        :params lv_name: volume name
        :return path: path for disk image
        """
        device_name = libvirt.setup_or_cleanup_iscsi(is_setup=True)
        lv_utils.vg_create(vg_name, device_name)
        size = kwargs.get("size", "50M")
        if kwargs.get("size"):
            kwargs.pop("size")
        path = libvirt.create_local_disk("lvm", size=size, vgname=vg_name,
                                         lvname=lv_name, **kwargs)

        return path

    @staticmethod
    def create_rbd_disk_path(params):
        """
        Prepare rbd type disk image path

        :params params: Dict with the test parameters.
        :return new_image_path: path for disk image
        """
        sec_dict = eval(params.get("sec_dict", '{}'))
        auth_key = params.get("auth_key")
        mon_host = params.get("mon_host")
        new_image_path = params.get("image_path")
        auth_username = params.get("auth_user")
        client_name = params.get("client_name")

        ceph.create_config_file(mon_host)
        params.update(
            {'keyfile': ceph.create_keyring_file(client_name, auth_key)})
        LOG.debug('Create key file :%s' % params.get('keyfile'))

        libvirt_secret.clean_up_secrets()
        sec_uuid = libvirt_secret.create_secret(sec_dict=sec_dict)
        virsh.secret_set_value(sec_uuid, auth_key, debug=True)

        return mon_host, auth_username, new_image_path

    @staticmethod
    def set_disk_xml(disk_dict):
        """
        Set vm disk xml by disk dict.

        :params disk_dict: dict to get disk xml
        :return: disk xml
        """
        new_disk = Disk()
        new_disk.setup_attrs(**disk_dict)

        return new_disk

    def prepare_relative_path(self, disk_type, **kwargs):
        """
        Prepare relative path

        :params disk_type: disk type
        :return: dest image for new disk and backing list
        """
        dst_img, backing_list = '', []
        if disk_type == "file":
            libvirt.create_local_disk(
                "file", path=self.base_dir + "/a/a", size="500M",
                disk_format="qcow2", **kwargs)

            dst_img, backing_list = libvirt_disk.make_relative_path_backing_files(
                self.vm, pre_set_root_dir=self.base_dir, origin_image="../a/a",
                back_chain_lenghth=3, **kwargs)

        elif disk_type == "block":

            libvirt.setup_or_cleanup_iscsi(is_setup=False)

            pool_dict = eval(self.params.get('pool_dict', '{}'))
            source_dict = eval(self.params.get('source_dict', '{}'))
            pool_name = self.create_pool_from_xml(pool_dict, source_dict)
            source_path = []
            for i in range(0, self.lv_num):
                source = "/dev/%s/%s" % (pool_name, self.lv_name+str(i))
                virsh.vol_create_as(self.lv_name+str(i), pool_name, "20M",
                                    "10M", "", debug=True)
                libvirt.create_local_disk("file", source, "10M", "qcow2")
                source_path.append(source)

            dst_img, backing_list = libvirt_disk.make_syslink_path_backing_files(
                self.base_dir, volume_path_list=source_path, qemu_rebase=True)

        elif disk_type == "rbd_with_auth":
            origin_image = self._pre_rbd_origin_image()
            dst_img, backing_list = libvirt_disk.\
                make_relative_path_backing_files(self.vm,
                                                 pre_set_root_dir=self.base_dir,
                                                 origin_image=origin_image,
                                                 back_chain_lenghth=2, **kwargs)

        return dst_img, backing_list

    def _pre_rbd_origin_image(self):
        """
        Create the first image for rbd type disk.
        """
        mon_host = self.params.get("mon_host")
        new_image_path = self.params.get("image_path")
        auth_key = self.params.get("auth_key")
        client_name = self.params.get("client_name")
        self.params.update(
            {'keyfile': ceph.create_keyring_file(client_name, auth_key)})

        process.run("mkdir %s" % (self.base_dir + "/c"))
        origin_image = self.base_dir + '/c/c'
        cmd = "qemu-img create -f qcow2 -F raw -o " \
              "backing_file=rbd:%s:mon_host=%s %s" % (new_image_path,
                                                      mon_host, origin_image)
        process.run(cmd, ignore_status=False, shell=True)

        return origin_image

    @staticmethod
    def create_pool_from_xml(pool_dict, source_dict):
        """
        Build pool from xml

        :param pool_dict: pool dict,
        :param source_dict: pool source dict
        """
        device_name = libvirt.setup_or_cleanup_iscsi(is_setup=True)
        source_dict['device_path'] = device_name
        pool_name = pool_dict['name']

        pool = pool_xml.PoolXML()
        pool.setup_attrs(**pool_dict)

        source_xml = pool_xml.SourceXML()
        source_xml.device_path = device_name
        source_xml.vg_name = "vg0"
        source_xml.format_type = "lvm2"

        pool.source = source_xml

        virsh.pool_define(pool.xml, debug=True, ignore_status=False)
        virsh.pool_build(pool_name, options='--overwrite', debug=True, ignore_status=False)
        virsh.pool_start(pool_name, debug=True, ignore_status=False)

        return pool_name
