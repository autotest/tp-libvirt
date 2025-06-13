import logging
import os
import re
import time

from avocado.utils import lv_utils
from avocado.utils import process

from virttest import ceph
from virttest import data_dir
from virttest import utils_misc
from virttest import virsh

from virttest.libvirt_xml import pool_xml
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_disk
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_libvirt import libvirt_secret
from virttest.utils_nbd import NbdExport
from virttest.utils_test import libvirt

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
        self.lv_backing = 'lv1'
        self.new_image_path = ''
        self.path_list = ''
        self.disk_backend = ''
        self.disk_size = "50M"

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
                if disk_type != 'rbd_with_auth' and backing_list != []:
                    backing_list.pop()
                for elem in backing_list:
                    source_ele = elem.find("source")
                    if source_ele is not None:
                        source_list.append(source_ele.get('file') or
                                           source_ele.get('name') or
                                           source_ele.get('dev') or
                                           source_ele.get('volume'))
                        if source_ele.find("dataStore"):
                            source_list.append(
                                source_ele.find("dataStore").find('source').get('file'))

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

        dest_obj, new_image_path = self.prepare_disk_obj(
            disk_type, disk_dict, new_image_path, **kwargs)

        libvirt.add_vm_device(vmxml, dest_obj)

        return new_image_path

    def prepare_disk_obj(self, disk_type, disk_dict, new_image_path='', **kwargs):
        """
        Prepare disk object

        :param disk_type: disk type
        :param disk_dict: dict to update disk
        :param new_image_path: new disk image if needed
        :param kwargs: optional keyword arguments.
        :return disk_obj: return disk object
        :return new_image_path: return the updated new image path of new disk
        """
        if disk_type == 'file':
            if not new_image_path:
                new_image_path = data_dir.get_data_dir() + '/test.img'
                size = kwargs.get("size", "50M")
                if kwargs.get("size"):
                    kwargs.pop("size")
                libvirt.create_local_disk(
                    "file", path=new_image_path, size=size,
                    disk_format=self.params.get("disk_image_format", 'qcow2'),
                    **kwargs)
            disk_dict.update({'source': {'attrs': {'file': new_image_path}}})

        elif disk_type == 'block':
            if not new_image_path:
                new_image_path = self.create_lvm_disk_path(
                    vg_name=self.vg_name, lv_name=self.lv_name,
                    simulated_iscsi=self.params.get("simulated_iscsi"), **kwargs)
                if self.params.get('convert_format'):
                    process.run("qemu-img create -f %s /dev/%s/%s %s" % (
                            self.params.get('convert_format'), self.vg_name,
                            self.lv_name, self.disk_size), shell=True)
            disk_dict.update({'source': {'attrs': {'dev': new_image_path}}})

        elif disk_type == 'volume':
            if not new_image_path:
                pool_name, new_image_path = self.create_volume_for_disk_path(
                    self.test, self.params, self.pool_name,
                    self.pool_type, self.pool_target, self.emulated_image,
                    **kwargs)
            disk_dict.update({'source': {'attrs': {'pool': self.pool_name,
                                                   'volume': new_image_path}}})
        elif disk_type == 'nfs':
            if not new_image_path:
                nfs_res = libvirt.setup_or_cleanup_nfs(is_setup=True,
                                                       is_mount=True)
                new_image_path = nfs_res['mount_dir'] + '/test.img'

                libvirt.create_local_disk("file", path=new_image_path,
                                          size=self.disk_size,
                                          disk_format="qcow2", **kwargs)
            disk_dict.update({'source': {'attrs': {'file': new_image_path}}})

        elif disk_type == 'rbd_with_auth':
            no_update_dict = kwargs.get("no_update_dict")
            if no_update_dict:
                pass
            else:
                mon_host, auth_username, new_image_path, _ = \
                    self.create_rbd_disk_path(self.params)
                disk_dict.update({'source': {
                    'attrs': {'protocol': "rbd", "name": new_image_path},
                    "hosts": [{"name": mon_host}],
                    "auth": {"auth_user": auth_username,
                             "secret_usage": "cephlibvirt",
                             "secret_type": "ceph"}}})
        elif disk_type == 'rbd_with_luks_and_auth':
            mon_host, auth_username, new_image_path, _ = \
                self.create_rbd_disk_path(self.params, create_img_by_qemu_cmd=True)
            disk_dict.update({'source': {
                'attrs': {'protocol': "rbd", "name": new_image_path},
                "hosts": [{"name": mon_host}],
                "auth": {"auth_user": auth_username,
                         "secret_usage": self.params.get("ceph_usage_name"),
                         "secret_type": "ceph"}}})
            self.add_luks_encryption_to_disk_dict(
                disk_dict, self.params.get("luks_sec_dict") % new_image_path)

        elif disk_type == "nbd":
            nbd_server_host = process.run('hostname', ignore_status=False,
                                          shell=True,
                                          verbose=True).stdout_text.strip()
            nbd_server_port = self.params.get("nbd_server_port", "10001")
            image_path = self.params.get(
                "image_path", data_dir.get_data_dir() + "/images/nbdtest.img")

            device_format = self.params.get("device_format", "qcow2")
            export_name = self.params.get("export_name", "new")
            tls_enabled = "yes" == self.params.get("enable_tls", "yes")
            image_size = kwargs.get("size",
                                    self.params.get("image_size", "500M"))

            self.disk_backend = NbdExport(
                image_path, image_format=device_format, port=nbd_server_port,
                image_size=image_size, export_name=export_name, tls=tls_enabled)
            self.disk_backend.start_nbd_server()
            utils_misc.wait_for(
                lambda: process.system('netstat -nlp | grep %s ' % nbd_server_port, ignore_status=True, shell=True) == 0, 10)

            disk_dict.update(
                {'source': {"attrs": {"protocol": "nbd",
                                      "tls": self.params.get("enable_tls", "yes"),
                                      "name": export_name},
                            "hosts": [{"name": nbd_server_host,
                                       "port": nbd_server_port}]},
                 })

        elif disk_type == "ssh_with_key":
            new_image_path, known_hosts_path, keyfile = \
                self.create_ssh_disk_path(self.params, **kwargs)
            ssh_user = os.environ.get("USER")
            disk_dict.update(
                {'source': {"attrs": {"protocol": "ssh",
                                      "name": new_image_path},
                            "identity": [{"username": "%s" % ssh_user,
                                          "keyfile": keyfile}],
                            "knownhosts": known_hosts_path,
                            "hosts": [{"name": "127.0.0.1",
                                       "port": "22"}]}})

        elif disk_type == "ssh_with_passwd":
            new_image_path, known_hosts_path, _ = \
                self.create_ssh_disk_path(self.params, **kwargs)
            disk_dict.update(
                {'source': {"attrs": {"protocol": "ssh",
                                      "name": new_image_path},
                            "knownhosts": known_hosts_path,
                            "hosts": [{"name": "127.0.0.1",
                                       "port": "22"}],
                            "auth": {"auth_user": self.params.get("iscsi_user"),
                                     "secret_usage": "libvirtiscsi",
                                     "secret_type": "iscsi"}}})

        disk_obj = libvirt_vmxml.create_vm_device_by_type("disk", disk_dict)
        self.test.log.debug('Create disk object is :%s', disk_obj)

        return disk_obj, new_image_path

    def add_luks_encryption_to_disk_dict(self, disk_dict, secret_dict):
        """
        Add luks encryption to disk dict.

        :param disk_dict: disk dict.
        :param secret_dict: secret dict to define a secret.
        """
        sec_uuid = libvirt_secret.create_secret(sec_dict=eval(secret_dict))
        virsh.secret_set_value(sec_uuid, self.params.get("secret_pwd"),
                               debug=True, ignore_status=False)
        disk_source = disk_dict["source"]
        disk_source.update(
            {"encryption": {"secret": {"type": "passphrase", "uuid": sec_uuid},
                            "encryption": "luks", "attrs": {"engine": "librbd"}}})
        return disk_dict

    def cleanup_disk_preparation(self, disk_type, **kwargs):
        """
        Clean up the preparation of different type disk

        :param disk_type: disk type
        """
        if disk_type == 'block':
            self.cleanup_block_disk_preparation(
                self.vg_name, self.lv_name,
                simulated_iscsi=self.params.get("simulated_iscsi"))

        elif disk_type == 'file':
            if os.path.exists(self.new_image_path):
                os.remove(self.new_image_path)

        elif disk_type == 'volume':
            pvt = libvirt.PoolVolumeTest(self.test, self.params)
            pvt.cleanup_pool(self.pool_name, self.pool_type, self.pool_target,
                             self.emulated_image)

        elif disk_type == 'nfs':
            libvirt.setup_or_cleanup_nfs(is_setup=False)

        elif disk_type in ["rbd_with_auth", "rbd_with_luks_and_auth"]:
            self.cleanup_rbd_disk_path(self.params)

        elif disk_type == "nbd":
            try:
                self.disk_backend.cleanup()
            except Exception as ndbEx:
                self.test.log.debug("Clean Up nbd failed: %s", str(ndbEx))

        elif disk_type == "ssh_with_key" or disk_type == "ssh_with_passwd":
            self.cleanup_ssh_disk_path(self.new_image_path, **kwargs)

    @staticmethod
    def cleanup_block_disk_preparation(vg_name, lv_name, **kwargs):
        """
        Clean up volume group, logical volume, iscsi target.

        :param vg_name: volume group name
        :param lv_name: volume name
        :param kwargs: optional keyword arguments.
        """
        lv_utils.lv_remove(vg_name, lv_name)
        lv_utils.vg_remove(vg_name)
        if kwargs.get("simulated_iscsi"):
            time.sleep(3)
            process.run("modprobe %s -r" % re.findall(
                r"modprobe (\S+)", kwargs.get("simulated_iscsi"))[0])
        else:
            libvirt.setup_or_cleanup_iscsi(is_setup=False)
            if os.path.exists('/dev/%s' % vg_name):
                os.rmdir('/dev/%s' % vg_name)

    @staticmethod
    def create_volume_for_disk_path(test, params, pool_name='pool_name',
                                    pool_type='dir', pool_target='dir_pool',
                                    emulated_image='dir_pool_img',
                                    vol_name='vol_name', capacity='2G',
                                    allocation='2G', volume_type='qcow2',
                                    **kwargs):
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
        if kwargs.get("simulated_iscsi"):
            process.run(kwargs.get("simulated_iscsi"), shell=True)
            device_name = get_simulated_iscsi()
        else:
            device_name = libvirt.setup_or_cleanup_iscsi(is_setup=True)
        lv_utils.vg_create(vg_name, device_name)
        size = kwargs.get("size", "200M")
        if kwargs.get("size"):
            kwargs.pop("size")
        path = libvirt.create_local_disk("lvm", size=size, vgname=vg_name,
                                         lvname=lv_name)

        return path

    @staticmethod
    def cleanup_ssh_disk_path(new_image_path, **kwargs):
        """
        Clean up ssh disk image

        :params new_image_path: the new image path.
        :param kwargs: optional keyword arguments.
        """
        keyfile = kwargs.get("keyfile")
        known_hosts_path = kwargs.get("known_hosts_path")
        for file in [keyfile, f"{keyfile}.pub", known_hosts_path, new_image_path]:
            if os.path.exists(file):
                os.remove(file)

    @staticmethod
    def cleanup_rbd_disk_path(params):
        """
        Clean up rbd disk image

        :params params: dict, test parameters
        """
        mon_host = params.get("mon_host")
        new_image_path = params.get("image_path")

        libvirt_secret.clean_up_secrets()
        ceph.rbd_image_rm(mon_host, new_image_path.split("/")[0],
                          new_image_path.split("/")[1])
        if os.path.exists(params.get('keyfile')):
            os.remove(params.get('keyfile'))
        if os.path.exists(params.get('configfile')):
            os.remove(params.get('configfile'))

    @staticmethod
    def create_rbd_disk_path(params, create_img_by_qemu_cmd=False):
        """
        Prepare rbd type disk image path

        :params params: Dict with the test parameters.
        :params create_img_by_qemu_cmd: The flag for whether creating rbd type
        image by qemu, default False
        :return: tuple, include monitor host, auth username,
                        new image path and secret uuid
        """
        sec_dict = eval(params.get("sec_dict", '{}'))
        auth_key = params.get("auth_key")
        mon_host = params.get("mon_host")
        rbd_image_size = params.get("rbd_image_size", '50M')
        new_image_path = params.get("image_path")
        auth_username = params.get("auth_user")
        client_name = params.get("client_name")

        params.update(
            {'configfile': ceph.create_config_file(mon_host)})
        params.update(
            {'keyfile': ceph.create_keyring_file(client_name, auth_key)})
        LOG.debug('Create key file :%s' % params.get('keyfile'))

        libvirt_secret.clean_up_secrets()
        sec_uuid = libvirt_secret.create_secret(sec_dict=sec_dict)
        virsh.secret_set_value(sec_uuid, auth_key, debug=True)

        if not create_img_by_qemu_cmd:
            ceph.rbd_image_rm(mon_host, new_image_path.split("/")[0],
                              new_image_path.split("/")[1])
            ceph.rbd_image_create(mon_host, new_image_path.split("/")[0],
                                  new_image_path.split("/")[1], rbd_image_size)
        else:
            libvirt.create_local_disk(
                disk_type="file", path='',
                extra=params.get("rbd_image_parameter").format(
                    new_image_path, auth_username, auth_key, mon_host),
                disk_format=params.get("rbd_image_format"), size=rbd_image_size)

        return mon_host, auth_username, new_image_path, sec_uuid

    @staticmethod
    def create_ssh_disk_path(params, **kwargs):
        """
        Prepare ssh type disk image path

        :params params: Dict with the test parameters.
        :param kwargs: optional keyword arguments.
        :params return: tuple, include new_image_path, known_hosts_path and keyfile.
        """
        authorized_path = "/root/.ssh/authorized_keys"
        sec_dict = eval(params.get("sec_dict", "{}"))
        secret_pwd = params.get("secret_pwd")
        new_image_path = params.get("new_image_path")
        with_secret_passwd = "yes" == params.get("with_secret_passwd", "no")

        if not new_image_path:
            new_image_path = data_dir.get_data_dir() + '/test.img'
            size = kwargs.get("size", "50M")
            if kwargs.get("size"):
                kwargs.pop("size")
            libvirt.create_local_disk(
                "file", path=new_image_path, size=size,
                disk_format=params.get("disk_image_format", 'qcow2'))

        keyfile = kwargs.get("keyfile")
        known_hosts_path = kwargs.get("known_hosts_path")
        for file in [keyfile, f"{keyfile}.pub", known_hosts_path]:
            if os.path.exists(file):
                os.remove(file)

        process.run("ssh-keygen -t rsa -q -N '' -f %s" % keyfile, shell=True)
        process.run("ssh-keyscan -p 22 127.0.0.1 >> %s" % known_hosts_path, shell=True)
        process.run("cat %s >> %s" % (f"{keyfile}.pub", authorized_path), shell=True)

        if with_secret_passwd:
            libvirt_secret.clean_up_secrets()
            sec_uuid = libvirt_secret.create_secret(sec_dict=sec_dict)
            virsh.secret_set_value(sec_uuid, secret_pwd,
                                   debug=True, ignore_status=False)
        return new_image_path, known_hosts_path, keyfile

    def prepare_relative_path(self, disk_type, **kwargs):
        """
        Prepare relative path

        :params disk_type: disk type
        :return: dest image for new disk and backing list
        """
        dst_img, backing_list = '', []
        if disk_type == "file":
            size = self.params.get("size", "200M")
            libvirt.create_local_disk(
                "file", path=self.base_dir + "/a/a", size=size,
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
                source = "/dev/%s/%s" % (pool_name, self.lv_name + str(i))
                virsh.vol_create_as(self.lv_name + str(i), pool_name, "20M",
                                    "10M", "", debug=True)
                libvirt.create_local_disk("file", source, "10M", "qcow2")
                source_path.append(source)

            dst_img, backing_list = libvirt_disk.make_syslink_path_backing_files(
                self.base_dir, volume_path_list=source_path, qemu_rebase=True)

        elif disk_type == "rbd_with_auth":
            origin_image = self._pre_rbd_origin_image()
            dst_img, backing_list = libvirt_disk. \
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
        rbd_image_size = self.params.get("rbd_image_size", "200M")

        self.params.update(
            {'keyfile': ceph.create_keyring_file(client_name, auth_key)})
        self.params.update(
            {'configfile': ceph.create_config_file(mon_host)})

        ceph.rbd_image_rm(mon_host, new_image_path.split("/")[0],
                          new_image_path.split("/")[1])
        ceph.rbd_image_create(mon_host, new_image_path.split("/")[0],
                              new_image_path.split("/")[1], rbd_image_size)

        process.run("mkdir %s" % (self.base_dir + "/c"))
        cmd = "cd %s && qemu-img create -f qcow2 -F raw -o " \
              "backing_file=rbd:%s:mon_host=%s %s" % (self.base_dir+"/c",
                                                      new_image_path,
                                                      mon_host, "c")
        origin_image = "../c/c"
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
        virsh.pool_build(pool_name, options='--overwrite', debug=True,
                         ignore_status=False)
        virsh.pool_start(pool_name, debug=True, ignore_status=False)

        return pool_name

    def prepare_backing_file(self, disk_type, backing_file_type, backing_format):
        """
        Prepare backing file

        :param disk_type: disk type
        :param backing_file_type: backing file type.
        :param backing_format: backing file format.
        :return: based image and the backing file of the based image
        """
        based_image, backing_file = '', ''

        if disk_type == 'file':
            based_image = data_dir.get_data_dir()+'/based.qcow2'
            if backing_file_type == 'file':
                backing_file = data_dir.get_data_dir()+'/backing.qcow2'
                libvirt.create_local_disk('file', backing_file,
                                          size='200M', disk_format="qcow2")
            if backing_file_type == 'block':
                backing_file = self.create_lvm_disk_path(self.vg_name, self.lv_backing)

        elif disk_type == 'block':
            based_image = self.create_lvm_disk_path(self.vg_name, self.lv_name)
            if backing_file_type == 'file':
                backing_file = data_dir.get_data_dir() + '/backing.qcow2'
                libvirt.create_local_disk('file', backing_file, '200M', "qcow2")
            if backing_file_type == 'block':
                backing_file = libvirt.create_local_disk(
                    "lvm", size="10M", vgname=self.vg_name, lvname=self.lv_backing)

        backing_cmd = "qemu-img create -f qcow2 -b %s -F %s %s" % (
            backing_file, backing_format, based_image)
        process.run(backing_cmd, shell=True, verbose=True)

        return based_image, backing_file


def get_simulated_iscsi():
    """
    Get simulated iscsi device.

    :return simulated iscsi device name.
    """
    res = process.run("lsscsi", shell=True).stdout_text.strip()
    device_name = re.findall(r"Linux\s*scsi_debug\s*\d+\s+(\S+)\s*", res)[0]
    return device_name
