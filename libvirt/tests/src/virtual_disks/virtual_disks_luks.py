import os
import logging
import aexpect
import platform
import time

from avocado.utils import process

from virttest import remote
from virttest import virt_vm
from virttest import virsh
from virttest import utils_package
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.disk import Disk

from provider import libvirt_version


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
        cmd = ("qemu-img create -f luks "
               "--object secret,id=sec0,data=`printf '%s' | base64`,format=base64 "
               "-o key-secret=sec0 %s %s" % (password, device, size))
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
        if '-U' in cmd_result.stdout:
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
            new_parts = libvirt.get_parts_list(session)
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

        except (remote.LoginError, virt_vm.VMError, aexpect.ShellError), e:
            logging.error(str(e))
            return False

    # Disk specific attributes.
    device = params.get("virt_disk_device", "disk")
    device_target = params.get("virt_disk_device_target", "vdd")
    device_format = params.get("virt_disk_device_format", "raw")
    device_type = params.get("virt_disk_device_type", "file")
    device_bus = params.get("virt_disk_device_bus", "virtio")
    backend_storage_type = params.get("backend_storage_type", "iscsi")

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
    check_partitions = "yes" == params.get("virt_disk_check_partitions", "yes")
    hotplug_disk = "yes" == params.get("hotplug_disk", "no")
    encryption_in_source = "yes" == params.get("encryption_in_source", "no")
    auth_in_source = "yes" == params.get("auth_in_source", "no")
    auth_sec_uuid = ""
    luks_sec_uuid = ""
    disk_auth_dict = {}
    disk_encryption_dict = {}
    pvt = None

    if ((encryption_in_source or auth_in_source) and
            not libvirt_version.version_compare(3, 9, 0)):
        test.cancel("Cannot put <encryption> or <auth> inside disk <source> "
                    "in this libvirt version.")
    # Start VM and get all partions in VM.
    if vm.is_dead():
        vm.start()
    session = vm.wait_for_login()
    old_parts = libvirt.get_parts_list(session)
    session.close()
    vm.destroy(gracefully=False)

    # Back up xml file.
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    try:
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
            gluster_host_ip = libvirt.setup_or_cleanup_gluster(
                    is_setup=True,
                    vol_name=gluster_vol_name,
                    pool_name=gluster_pool_name)
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
            key_file = os.path.join(test.tmpdir, "ceph.key")
            key_opt = ""
            if not utils_package.package_install(["ceph-common"]):
                test.error("Failed to install ceph-common")
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
                    cmd = ("rbd -m {0} {1} info {2} && rbd -m {0} {1} rm "
                           "{2}".format(ceph_mon_ip, key_opt, ceph_disk_name))
                else:
                    test.error("No ceph client name/key provided.")
                device_source = "rbd:%s:mon_host=%s:keyring=%s" % (ceph_disk_name,
                                                                   ceph_mon_ip,
                                                                   key_file)
            else:
                device_source = "rbd:%s:mon_host=%s" % (ceph_disk_name, ceph_mon_ip)
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
            tmp_dir = test.tmpdir
            pvt = libvirt.PoolVolumeTest(test, params)
            pvt.pre_pool(pool_name, pool_type, pool_target, emulated_image)
            nfs_mount_dir = os.path.join(tmp_dir, pool_target)
            device_source = nfs_mount_dir + image_name
            disk_src_dict = {'attrs': {'file': device_source,
                                       'type_name': 'file'}}
        else:
            test.cancel("Only iscsi/gluster/rbd/nfs can be tested for now.")
        logging.debug("device source is: %s", device_source)
        luks_sec_uuid = libvirt.create_secret(params)
        logging.debug("A secret created with uuid = '%s'", luks_sec_uuid)
        ret = virsh.secret_set_value(luks_sec_uuid, luks_secret_passwd,
                                     encode=True, debug=True)
        encrypt_dev(device_source, params)
        libvirt.check_exit_status(ret)
        # Add disk xml.
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        disk_xml = Disk(type_name=device_type)
        disk_xml.device = device
        disk_xml.target = {"dev": device_target, "bus": device_bus}
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
        disk_xml.source = disk_source
        logging.debug("new disk xml is: %s", disk_xml)
        # Sync VM xml
        if not hotplug_disk:
            vmxml.add_device(disk_xml)
        vmxml.sync()
        try:
            vm.start()
            vm.wait_for_login()
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
        check_dev_format(device_source)
    finally:
        # Recover VM.
        if vm.is_alive():
            vm.destroy(gracefully=False)
        vmxml_backup.sync("--snapshots-metadata")

        # Clean up backend storage
        if backend_storage_type == "iscsi":
            libvirt.setup_or_cleanup_iscsi(is_setup=False)
        elif backend_storage_type == "gluster":
            libvirt.setup_or_cleanup_gluster(is_setup=False,
                                             vol_name=gluster_vol_name,
                                             pool_name=gluster_pool_name)
        elif backend_storage_type == "ceph":
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
