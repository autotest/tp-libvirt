import os
import re
import logging
import aexpect
import platform
import time

from avocado.utils import process

from virttest import remote
from virttest import data_dir
from virttest import virt_vm
from virttest import virsh
from virttest import utils_package
from virttest import ceph
from virttest import gluster
from virttest import utils_disk
from virttest import libvirt_storage
from virttest.utils_test import libvirt

from virttest.libvirt_xml import vm_xml, vol_xml, xcepts
from virttest.libvirt_xml.devices.disk import Disk

from virttest import libvirt_version

TMP_DATA_DIR = data_dir.get_data_dir()


def run(test, params, env):
    """
    Test disk encryption option.

    1.Prepare backend storage (blkdev/iscsi/gluster/ceph)
    2.Use luks format to encrypt the backend storage
    3.Prepare a disk xml indicating to the backend storage with valid/invalid
      luks password
    4.Start VM with disk hot/cold plugged
    5.Check some disk operations in VM
    6.Check backend storage is still in luks format
    7.Recover test environment
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    virsh_dargs = {'debug': True, 'ignore_status': True}

    def encrypt_dev(device, params):
        """
        Encrypt device with luks format

        :param device: Storage deivce to be encrypted.
        :param params: From the dict to get encryption password.
        """
        password = params.get("luks_encrypt_passwd", "password")
        size = params.get("luks_size", "500M")
        preallocation = params.get("preallocation")
        cmd = ("qemu-img create -f luks "
               "--object secret,id=sec0,data=`printf '%s' | base64`,format=base64 "
               "-o key-secret=sec0 %s %s" % (password, device, size))
        # Add preallocation if it is given in params
        if preallocation:
            cmd = cmd.replace("key-secret=sec0", "key-secret=sec0,preallocation=%s" % preallocation)
        if process.system(cmd, shell=True):
            test.fail("Can't create a luks encrypted img by qemu-img")

    def check_dev_format(device, fmt="luks"):
        """
        Check if device is in luks format

        :param device: Storage deivce to be checked.
        :param fmt: Expected disk format.
        :return: If device's format equals to fmt, return True, else return False.
        """
        cmd_result = process.run("qemu-img" + ' -h', ignore_status=True,
                                 shell=True, verbose=False)
        if b'-U' in cmd_result.stdout:
            cmd = ("qemu-img info -U %s| grep -i 'file format' "
                   "| grep -i %s" % (device, fmt))
        else:
            cmd = ("qemu-img info %s| grep -i 'file format' "
                   "| grep -i %s" % (device, fmt))
        cmd_result = process.run(cmd, ignore_status=True, shell=True)
        if cmd_result.exit_status:
            test.fail("device %s is not in %s format. err is: %s" %
                      (device, fmt, cmd_result.stderr))

    def check_in_vm(target, old_parts):
        """
        Check mount/read/write disk in VM.

        :param target: Disk dev in VM.
        :param old_parts: Original disk partitions in VM.
        :return: True if check successfully.
        """
        try:
            session = vm.wait_for_login()
            if platform.platform().count('ppc64'):
                time.sleep(10)
            new_parts = utils_disk.get_parts_list(session)
            added_parts = list(set(new_parts).difference(set(old_parts)))
            logging.info("Added parts:%s", added_parts)
            if len(added_parts) != 1:
                logging.error("The number of new partitions is invalid in VM")
                return False
            else:
                added_part = added_parts[0]
            cmd = ("fdisk -l /dev/{0} && mkfs.ext4 -F /dev/{0} && "
                   "mkdir -p test && mount /dev/{0} test && echo"
                   " teststring > test/testfile && umount test"
                   .format(added_part))
            status, output = session.cmd_status_output(cmd)
            logging.debug("Disk operation in VM:\nexit code:\n%s\noutput:\n%s",
                          status, output)
            return status == 0

        except (remote.LoginError, virt_vm.VMError, aexpect.ShellError) as e:
            logging.error(str(e))
            return False

    def create_vol(p_name, target_encrypt_params, vol_params):
        """
        Create volume.

        :param p_name: Pool name.
        :param target_encrypt_params: encrypt parameters in dict.
        :param vol_params: Volume parameters dict.
        """
        # Clean up dirty volumes if pool has.
        pv = libvirt_storage.PoolVolume(p_name)
        vol_name_list = pv.list_volumes()
        for vol_name in vol_name_list:
            pv.delete_volume(vol_name)

        volxml = vol_xml.VolXML()
        v_xml = volxml.new_vol(**vol_params)
        v_xml.encryption = volxml.new_encryption(**target_encrypt_params)
        v_xml.xmltreefile.write()

        ret = virsh.vol_create(p_name, v_xml.xml, **virsh_dargs)
        libvirt.check_exit_status(ret)

    def get_secret_list():
        """
        Get secret list.

        :return secret list
        """
        logging.info("Get secret list ...")
        secret_list_result = virsh.secret_list()
        secret_list = secret_list_result.stdout.strip().splitlines()
        # First two lines contain table header followed by entries
        # for each secret, such as:
        #
        # UUID                                  Usage
        # --------------------------------------------------------------------------------
        # b4e8f6d3-100c-4e71-9f91-069f89742273  ceph client.libvirt secret
        secret_list = secret_list[2:]
        result = []
        # If secret list is empty.
        if secret_list:
            for line in secret_list:
                # Split on whitespace, assume 1 column
                linesplit = line.split(None, 1)
                result.append(linesplit[0])
        return result

    def check_top_image_in_xml(expected_top_image):
        """
        check top image in src file

        :param expected_top_image: expect top image
        """
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        disks = vmxml.devices.by_device_tag('disk')
        disk_xml = None
        for disk in disks:
            if disk.target['dev'] != device_target:
                continue
            else:
                disk_xml = disk.xmltreefile
                break
        logging.debug("disk xml in top: %s\n", disk_xml)
        src_file = disk_xml.find('source').get('file')
        if src_file is None:
            src_file = disk_xml.find('source').get('name')
        if src_file != expected_top_image:
            test.fail("Current top img %s is not the same with %s"
                      % (src_file, expected_top_image))

    # Disk specific attributes.
    device = params.get("virt_disk_device", "disk")
    device_target = params.get("virt_disk_device_target", "vdd")
    device_type = params.get("virt_disk_device_type", "file")
    device_format = params.get("virt_disk_device_format", "raw")
    device_bus = params.get("virt_disk_device_bus", "virtio")
    backend_storage_type = params.get("backend_storage_type", "iscsi")
    volume_target_format = params.get("target_format", "raw")

    # Backend storage options.
    storage_size = params.get("storage_size", "1G")
    enable_auth = "yes" == params.get("enable_auth")

    # Luks encryption info, luks_encrypt_passwd is the password used to encrypt
    # luks image, and luks_secret_passwd is the password set to luks secret, you
    # can set a wrong password to luks_secret_passwd for negative tests
    luks_encrypt_passwd = params.get("luks_encrypt_passwd", "password")
    luks_secret_passwd = params.get("luks_secret_passwd", "password")
    # Backend storage auth info
    use_auth_usage = "yes" == params.get("use_auth_usage")
    if use_auth_usage:
        use_auth_uuid = False
    else:
        use_auth_uuid = "yes" == params.get("use_auth_uuid", "yes")
    auth_sec_usage_type = params.get("auth_sec_usage_type", "iscsi")
    auth_sec_usage_target = params.get("auth_sec_usage_target", "libvirtiscsi")

    status_error = "yes" == params.get("status_error")
    define_error = "yes" == params.get("define_error")
    check_partitions = "yes" == params.get("virt_disk_check_partitions", "yes")
    hotplug_disk = "yes" == params.get("hotplug_disk", "no")
    encryption_in_source = "yes" == params.get("encryption_in_source", "no")
    auth_in_source = "yes" == params.get("auth_in_source", "no")
    auth_sec_uuid = ""
    luks_sec_uuid = ""
    disk_auth_dict = {}
    disk_encryption_dict = {}
    pvt = None
    duplicated_encryption = "yes" == params.get("duplicated_encryption", "no")
    slice_support_enable = "yes" == params.get("slice_support_enable", "no")
    block_copy_test = "yes" == params.get("block_copy_test", "no")

    if ((encryption_in_source or auth_in_source) and
            not libvirt_version.version_compare(3, 9, 0)):
        test.cancel("Cannot put <encryption> or <auth> inside disk <source> "
                    "in this libvirt version.")
    # Start VM and get all partions in VM.
    if vm.is_dead():
        vm.start()
    session = vm.wait_for_login()
    old_parts = utils_disk.get_parts_list(session)
    session.close()
    vm.destroy(gracefully=False)

    # Back up xml file.
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    try:
        # Clean up dirty secrets in test environments if there are.
        dirty_secret_list = get_secret_list()
        if dirty_secret_list:
            for dirty_secret_uuid in dirty_secret_list:
                virsh.secret_undefine(dirty_secret_uuid)
        # Create secret
        luks_sec_uuid = libvirt.create_secret(params)
        logging.debug("A secret created with uuid = '%s'", luks_sec_uuid)
        ret = virsh.secret_set_value(luks_sec_uuid, luks_secret_passwd,
                                     encode=True, debug=True)
        libvirt.check_exit_status(ret)
        # Setup backend storage
        if backend_storage_type == "iscsi":
            iscsi_host = params.get("iscsi_host")
            iscsi_port = params.get("iscsi_port")
            if device_type == "block":
                device_source = libvirt.setup_or_cleanup_iscsi(is_setup=True)
                disk_src_dict = {'attrs': {'dev': device_source}}
            elif device_type == "network":
                if enable_auth:
                    chap_user = params.get("chap_user", "redhat")
                    chap_passwd = params.get("chap_passwd", "password")
                    auth_sec_usage = params.get("auth_sec_usage",
                                                "libvirtiscsi")
                    auth_sec_dict = {"sec_usage": "iscsi",
                                     "sec_target": auth_sec_usage}
                    auth_sec_uuid = libvirt.create_secret(auth_sec_dict)
                    # Set password of auth secret (not luks encryption secret)
                    virsh.secret_set_value(auth_sec_uuid, chap_passwd,
                                           encode=True, debug=True)
                    iscsi_target, lun_num = libvirt.setup_or_cleanup_iscsi(
                        is_setup=True, is_login=False, image_size=storage_size,
                        chap_user=chap_user, chap_passwd=chap_passwd,
                        portal_ip=iscsi_host)
                    # ISCSI auth attributes for disk xml
                    if use_auth_uuid:
                        disk_auth_dict = {"auth_user": chap_user,
                                          "secret_type": auth_sec_usage_type,
                                          "secret_uuid": auth_sec_uuid}
                    elif use_auth_usage:
                        disk_auth_dict = {"auth_user": chap_user,
                                          "secret_type": auth_sec_usage_type,
                                          "secret_usage": auth_sec_usage_target}
                else:
                    iscsi_target, lun_num = libvirt.setup_or_cleanup_iscsi(
                        is_setup=True, is_login=False, image_size=storage_size,
                        portal_ip=iscsi_host)
                device_source = "iscsi://%s:%s/%s/%s" % (iscsi_host, iscsi_port,
                                                         iscsi_target, lun_num)
                disk_src_dict = {"attrs": {"protocol": "iscsi",
                                           "name": "%s/%s" % (iscsi_target, lun_num)},
                                 "hosts": [{"name": iscsi_host, "port": iscsi_port}]}
        elif backend_storage_type == "gluster":
            gluster_vol_name = params.get("gluster_vol_name", "gluster_vol1")
            gluster_pool_name = params.get("gluster_pool_name", "gluster_pool1")
            gluster_img_name = params.get("gluster_img_name", "gluster1.img")
            gluster_host_ip = gluster.setup_or_cleanup_gluster(
                    is_setup=True,
                    vol_name=gluster_vol_name,
                    pool_name=gluster_pool_name,
                    **params)
            device_source = "gluster://%s/%s/%s" % (gluster_host_ip,
                                                    gluster_vol_name,
                                                    gluster_img_name)
            disk_src_dict = {"attrs": {"protocol": "gluster",
                                       "name": "%s/%s" % (gluster_vol_name,
                                                          gluster_img_name)},
                             "hosts":  [{"name": gluster_host_ip,
                                         "port": "24007"}]}
        elif backend_storage_type == "ceph":
            ceph_host_ip = params.get("ceph_host_ip", "EXAMPLE_HOSTS")
            ceph_mon_ip = params.get("ceph_mon_ip", "EXAMPLE_MON_HOST")
            ceph_host_port = params.get("ceph_host_port", "EXAMPLE_PORTS")
            ceph_disk_name = params.get("ceph_disk_name", "EXAMPLE_SOURCE_NAME")
            ceph_client_name = params.get("ceph_client_name")
            ceph_client_key = params.get("ceph_client_key")
            ceph_auth_user = params.get("ceph_auth_user")
            ceph_auth_key = params.get("ceph_auth_key")
            enable_auth = "yes" == params.get("enable_auth")
            key_file = os.path.join(TMP_DATA_DIR, "ceph.key")
            key_opt = ""
            # Prepare a blank params to confirm if delete the configure at the end of the test
            ceph_cfg = ""
            if not utils_package.package_install(["ceph-common"]):
                test.error("Failed to install ceph-common")
            # Create config file if it doesn't exist
            ceph_cfg = ceph.create_config_file(ceph_mon_ip)
            if enable_auth:
                # If enable auth, prepare a local file to save key
                if ceph_client_name and ceph_client_key:
                    with open(key_file, 'w') as f:
                        f.write("[%s]\n\tkey = %s\n" %
                                (ceph_client_name, ceph_client_key))
                    key_opt = "--keyring %s" % key_file
                    auth_sec_dict = {"sec_usage": auth_sec_usage_type,
                                     "sec_name": "ceph_auth_secret"}
                    auth_sec_uuid = libvirt.create_secret(auth_sec_dict)
                    virsh.secret_set_value(auth_sec_uuid, ceph_auth_key,
                                           debug=True)
                    disk_auth_dict = {"auth_user": ceph_auth_user,
                                      "secret_type": auth_sec_usage_type,
                                      "secret_uuid": auth_sec_uuid}
                else:
                    test.error("No ceph client name/key provided.")
                device_source = "rbd:%s:mon_host=%s:keyring=%s" % (ceph_disk_name,
                                                                   ceph_mon_ip,
                                                                   key_file)
            else:
                device_source = "rbd:%s:mon_host=%s" % (ceph_disk_name, ceph_mon_ip)
            cmd = ("rbd -m {0} {1} info {2} && rbd -m {0} {1} rm "
                   "{2}".format(ceph_mon_ip, key_opt, ceph_disk_name))
            cmd_result = process.run(cmd, ignore_status=True, shell=True)
            logging.debug("pre clean up rbd disk if exists: %s", cmd_result)
            disk_src_dict = {"attrs": {"protocol": "rbd",
                                       "name": ceph_disk_name},
                             "hosts":  [{"name": ceph_host_ip,
                                         "port": ceph_host_port}]}
        elif backend_storage_type == "nfs":
            pool_name = params.get("pool_name", "nfs_pool")
            pool_target = params.get("pool_target", "nfs_mount")
            pool_type = params.get("pool_type", "netfs")
            nfs_server_dir = params.get("nfs_server_dir", "nfs_server")
            emulated_image = params.get("emulated_image")
            image_name = params.get("nfs_image_name", "nfs.img")
            tmp_dir = TMP_DATA_DIR
            pvt = libvirt.PoolVolumeTest(test, params)
            pvt.pre_pool(pool_name, pool_type, pool_target, emulated_image)
            nfs_mount_dir = os.path.join(tmp_dir, pool_target)
            device_source = nfs_mount_dir + image_name
            disk_src_dict = {'attrs': {'file': device_source,
                                       'type_name': 'file'}}
        # Create dir based pool,and then create one volume on it.
        elif backend_storage_type == "dir":
            pool_name = params.get("pool_name", "dir_pool")
            pool_target = params.get("pool_target")
            pool_type = params.get("pool_type")
            emulated_image = params.get("emulated_image")
            image_name = params.get("dir_image_name", "luks_1.img")
            # Create and start dir_based pool.
            pvt = libvirt.PoolVolumeTest(test, params)
            if not os.path.exists(pool_target):
                os.mkdir(pool_target)
            pvt.pre_pool(pool_name, pool_type, pool_target, emulated_image)
            sp = libvirt_storage.StoragePool()
            if not sp.is_pool_active(pool_name):
                sp.set_pool_autostart(pool_name)
                sp.start_pool(pool_name)
            # Create one volume on the pool.
            volume_name = params.get("vol_name")
            volume_alloc = params.get("vol_alloc")
            volume_cap_unit = params.get("vol_cap_unit")
            volume_cap = params.get("vol_cap")
            volume_target_path = params.get("sec_volume")
            volume_target_format = params.get("target_format")
            device_format = volume_target_format
            volume_target_encypt = params.get("target_encypt", "")
            volume_target_label = params.get("target_label")
            vol_params = {"name": volume_name, "capacity": int(volume_cap),
                          "allocation": int(volume_alloc), "format":
                          volume_target_format, "path": volume_target_path,
                          "label": volume_target_label,
                          "capacity_unit": volume_cap_unit}
            vol_encryption_params = {}
            vol_encryption_params.update({"format": "luks"})
            vol_encryption_params.update({"secret": {"type": "passphrase", "uuid": luks_sec_uuid}})
            try:
                # If target format is qcow2,need to create test image with "qemu-img create"
                if volume_target_format == "qcow2":
                    option = params.get("luks_extra_elements")
                    libvirt.create_local_disk("file", path=volume_target_path, extra=option,
                                              disk_format="qcow2", size="1")
                else:
                    # If Libvirt version is lower than 2.5.0
                    # Creating luks encryption volume is not supported,so skip it.
                    create_vol(pool_name, vol_encryption_params, vol_params)
            except AssertionError as info:
                err_msgs = ("create: invalid option")
                if str(info).count(err_msgs):
                    test.cancel("Creating luks encryption volume "
                                "is not supported on this libvirt version")
                else:
                    test.error("Failed to create volume."
                               "Error: %s" % str(info))
            disk_src_dict = {'attrs': {'file': volume_target_path}}
            device_source = volume_target_path
        elif backend_storage_type == "file":
            tmp_dir = TMP_DATA_DIR
            image_name = params.get("file_image_name", "slice.img")
            device_source = os.path.join(tmp_dir, image_name)
            disk_src_dict = {'attrs': {'file': device_source}}
        else:
            test.cancel("Only iscsi/gluster/rbd/nfs/file can be tested for now.")
        logging.debug("device source is: %s", device_source)
        if backend_storage_type != "dir":
            encrypt_dev(device_source, params)
        # Add disk xml.
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        disk_xml = Disk(type_name=device_type)
        disk_xml.device = device
        disk_xml.target = {"dev": device_target, "bus": device_bus}
        print()
        driver_dict = {"name": "qemu", "type": device_format}
        disk_xml.driver = driver_dict
        disk_source = disk_xml.new_disk_source(**disk_src_dict)
        if disk_auth_dict:
            logging.debug("disk auth dict is: %s" % disk_auth_dict)
            if auth_in_source:
                disk_source.auth = disk_xml.new_auth(**disk_auth_dict)
            else:
                disk_xml.auth = disk_xml.new_auth(**disk_auth_dict)
        disk_encryption_dict = {"encryption": "luks",
                                "secret": {"type": "passphrase",
                                           "uuid": luks_sec_uuid}}
        disk_encryption = disk_xml.new_encryption(**disk_encryption_dict)
        if encryption_in_source:
            disk_source.encryption = disk_encryption
        else:
            disk_xml.encryption = disk_encryption
        if duplicated_encryption:
            disk_xml.encryption = disk_encryption
        if slice_support_enable:
            if not libvirt_version.version_compare(6, 0, 0):
                test.cancel("Cannot put <slice> inside disk <source> "
                            "in this libvirt version.")
            else:
                check_du_output = process.run("du -b %s" % device_source, shell=True).stdout_text
                slice_size = re.findall(r'[0-9]+', check_du_output)
                disk_source.slices = disk_xml.new_slices(
                        **{"slice_type": "storage", "slice_offset": "0", "slice_size": slice_size[0]})
        disk_xml.source = disk_source
        logging.debug("new disk xml is: %s", disk_xml)
        # Sync VM xml
        if not hotplug_disk:
            vmxml.add_device(disk_xml)
        try:
            vmxml.sync()
            vm.start()
            vm.wait_for_login()
        except xcepts.LibvirtXMLError as xml_error:
            if not define_error:
                test.fail("Failed to define VM:\n%s" % str(xml_error))
        except virt_vm.VMStartError as details:
            # When use wrong password in disk xml for cold plug cases,
            # VM cannot be started
            if status_error and not hotplug_disk:
                logging.info("VM failed to start as expected: %s" % str(details))
            else:
                test.fail("VM should start but failed: %s" % str(details))
        if hotplug_disk:
            result = virsh.attach_device(vm_name, disk_xml.xml,
                                         ignore_status=True, debug=True)
            libvirt.check_exit_status(result, status_error)
        if check_partitions and not status_error:
            if not check_in_vm(device_target, old_parts):
                test.fail("Check disk partitions in VM failed")
        if volume_target_format == "qcow2":
            check_dev_format(device_source, fmt="qcow2")
        else:
            check_dev_format(device_source)
        if block_copy_test:
            # Create a transient VM
            transient_vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
            if vm.is_alive():
                vm.destroy(gracefully=False)
            virsh.undefine(vm_name, debug=True, ignore_status=False)
            virsh.create(transient_vmxml.xml, ignore_status=False, debug=True)
            expected_top_image = vm.get_blk_devices()[device_target].get('source')
            options = params.get("blockcopy_options")
            tmp_dir = TMP_DATA_DIR
            tmp_file = time.strftime("%Y-%m-%d-%H.%M.%S.img")
            dest_path = os.path.join(tmp_dir, tmp_file)

            # Need cover a few scenarios:single blockcopy, blockcopy and abort combined
            virsh.blockcopy(vm_name, device_target, dest_path,
                            options, ignore_status=False, debug=True)
            if encryption_in_source:
                virsh.blockjob(vm_name, device_target, " --pivot", ignore_status=False)
                expected_top_image = dest_path
            else:
                virsh.blockjob(vm_name, device_target, " --abort", ignore_status=False)
            check_top_image_in_xml(expected_top_image)
    finally:
        # Recover VM.
        if vm.is_alive():
            vm.destroy(gracefully=False)
        vmxml_backup.sync("--snapshots-metadata")

        # Clean up backend storage
        if backend_storage_type == "iscsi":
            libvirt.setup_or_cleanup_iscsi(is_setup=False)
        elif backend_storage_type == "gluster":
            gluster.setup_or_cleanup_gluster(is_setup=False,
                                             vol_name=gluster_vol_name,
                                             pool_name=gluster_pool_name,
                                             **params)
        elif backend_storage_type == "ceph":
            # Remove ceph configure file if created.
            if ceph_cfg:
                os.remove(ceph_cfg)
            cmd = ("rbd -m {0} {1} info {2} && rbd -m {0} {1} rm "
                   "{2}".format(ceph_mon_ip, key_opt, ceph_disk_name))
            cmd_result = process.run(cmd, ignore_status=True, shell=True)
            logging.debug("result of rbd removal: %s", cmd_result)
            if os.path.exists(key_file):
                os.remove(key_file)

        # Clean up secrets
        if auth_sec_uuid:
            virsh.secret_undefine(auth_sec_uuid)
        if luks_sec_uuid:
            virsh.secret_undefine(luks_sec_uuid)

        # Clean up pools
        if pvt:
            pvt.cleanup_pool(pool_name, pool_type, pool_target, emulated_image)
