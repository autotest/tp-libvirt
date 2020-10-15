import os
import logging
import aexpect
import threading
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
from virttest.utils_nbd import NbdExport

from virttest.libvirt_xml import vm_xml, vol_xml, xcepts
from virttest.libvirt_xml.devices.disk import Disk

from virttest import libvirt_version
from virttest import utils_secret


def run(test, params, env):
    """
    Test virsh domblkthreshold option.

    1.Prepare backend storage (file/luks/iscsi/gluster/ceph/nbd)
    2.Start VM
    3.Set domblkthreshold on target device in VM
    4.Trigger one threshold event
    5.Check threshold event is received as expected
    6.Clean up test environment
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    virsh_dargs = {'debug': True, 'ignore_status': True}
    block_threshold_timeout = params.get("block_threshold_timeout", "120")
    event_type = params.get("event_type", "block-threshold")
    block_threshold_option = params.get("block_threshold_option", "--loop")

    def set_vm_block_domblkthreshold(vm_name, target_device, threshold, **dargs):
        """
        Set VM block threshold on specific target device.

        :param vm_name: VM name.
        :param target_device: target device in VM
        :param threshold: threshold value with specific unit such as 100M
        :param dargs: mutable parameter dict
        """
        ret = virsh.domblkthreshold(vm_name, target_device, threshold, **dargs)
        libvirt.check_exit_status(ret)

    def trigger_block_threshold_event(vm_domain, target):
        """
        Trigger block threshold event.

        :param vm_domain: VM name
        :param target: Disk dev in VM.
        """
        try:
            session = vm_domain.wait_for_login()
            time.sleep(10)
            cmd = ("fdisk -l /dev/{0} && mkfs.ext4 -F /dev/{0} && "
                   " mount /dev/{0} /mnt && "
                   " dd if=/dev/urandom of=/mnt/bigfile bs=1M count=101"
                   .format(target))
            status, output = session.cmd_status_output(cmd)
            if status:
                test.error("Failed to mount and fill data in VM: %s" % output)
        except (remote.LoginError, virt_vm.VMError, aexpect.ShellError) as e:
            logging.error(str(e))
            raise

    def check_threshold_event(vm_name, event_type, event_timeout, options, **dargs):
        """
        Check threshold event.

        :param vm_name: VM name
        :param event_type: event type.
        :param event_timeout: event timeout value
        :param options: event option
        :dargs: dynamic parameters.
        """
        ret = virsh.event(vm_name, event_type, event_timeout, options, **dargs)
        logging.debug(ret.stdout_text)
        libvirt.check_exit_status(ret)

    def create_vol(p_name, vol_params):
        """
        Create volume.

        :param p_name: Pool name.
        :param vol_params: Volume parameters dict.
        """
        # Clean up dirty volumes if pool has.
        pv = libvirt_storage.PoolVolume(p_name)
        vol_name_list = pv.list_volumes()
        for vol_name in vol_name_list:
            pv.delete_volume(vol_name)

        volxml = vol_xml.VolXML()
        v_xml = volxml.new_vol(**vol_params)
        v_xml.xmltreefile.write()

        ret = virsh.vol_create(p_name, v_xml.xml, **virsh_dargs)
        libvirt.check_exit_status(ret)

    def trigger_block_commit(vm_name, target, blockcommit_options, **virsh_dargs):
        """
        Trigger blockcommit.

        :param vm_name: VM name
        :param target: Disk dev in VM.
        :param blockcommit_options: blockcommit option
        :param virsh_dargs: additional parameters
        """
        result = virsh.blockcommit(vm_name, target,
                                   blockcommit_options, ignore_status=False, **virsh_dargs)

    def trigger_block_copy(vm_name, target, dest_path, blockcopy_options, **virsh_dargs):
        """
        Trigger blockcopy

        :param vm_name: string, VM name
        :param target: string, target disk
        :param dest_path: string, the path of copied disk
        :param blockcopy_options: string, some options applied
        :param virsh_dargs: additional options
        """
        result = virsh.blockcopy(vm_name, target, dest_path, blockcopy_options, **virsh_dargs)
        libvirt.check_exit_status(result)

    def trigger_mirror_threshold_event(vm_domain, target):
        """
        Trigger mirror mode block threshold event.

        :param vm_domain: VM name
        :param target: Disk target in VM.
        """
        try:
            session = vm_domain.wait_for_login()
            # Sleep 10 seconds to let wait for events thread start first in main thread
            time.sleep(10)
            cmd = ("dd if=/dev/urandom of=file bs=1G count=3")
            status, output = session.cmd_status_output(cmd)
            if status:
                test.error("Failed to fill data in VM target: %s with %s" % (target, output))
        except (remote.LoginError, virt_vm.VMError, aexpect.ShellError) as e:
            logging.error(str(e))
            raise
        except Exception as ex:
            raise

    def get_mirror_source_index(vm_name, dev_index=0):
        """
        Get mirror source index

        :param vm_name: VM name
        :param dev_index: Disk device index.
        :return mirror source index in integer
        """
        disk_list = vm_xml.VMXML.get_disk_source(vm_name)
        disk_mirror = disk_list[dev_index].find('mirror')
        if disk_mirror is None:
            test.fail("Failed to get disk mirror")
        disk_mirror_source = disk_mirror.find('source')
        return int(disk_mirror_source.get('index'))

    # Disk specific attributes.
    device = params.get("virt_disk_device", "disk")
    device_target = params.get("virt_disk_device_target", "vdd")
    device_format = params.get("virt_disk_device_format", "raw")
    device_type = params.get("virt_disk_device_type", "file")
    device_bus = params.get("virt_disk_device_bus", "virtio")
    backend_storage_type = params.get("backend_storage_type", "iscsi")

    # Backend storage auth info
    storage_size = params.get("storage_size", "1G")
    enable_auth = "yes" == params.get("enable_auth")
    use_auth_usage = "yes" == params.get("use_auth_usage")
    auth_sec_usage_type = params.get("auth_sec_usage_type", "iscsi")
    auth_sec_usage_target = params.get("auth_sec_usage_target", "libvirtiscsi")
    auth_sec_uuid = ""
    luks_sec_uuid = ""
    disk_auth_dict = {}
    disk_encryption_dict = {}

    status_error = "yes" == params.get("status_error")
    define_error = "yes" == params.get("define_error")

    mirror_mode_blockcommit = "yes" == params.get("mirror_mode_blockcommit", "no")
    mirror_mode_blockcopy = "yes" == params.get("mirror_mode_blockcopy", "no")
    default_snapshot_test = "yes" == params.get("default_snapshot_test", "no")
    block_threshold_value = params.get("block_threshold_value", "100M")
    snapshot_external_disks = []
    tmp_dir = data_dir.get_tmp_dir()
    dest_path = params.get("dest_path", "/var/lib/libvirt/images/newclone")

    pvt = None
    # Initialize one NbdExport object
    nbd = None
    img_file = os.path.join(data_dir.get_tmp_dir(),
                            "%s_test.img" % vm_name)
    if ((backend_storage_type == "luks") and
            not libvirt_version.version_compare(3, 9, 0)):
        test.cancel("Cannot support <encryption> inside disk in this libvirt version.")

    # Start VM and get all partitions in VM.
    if vm.is_dead():
        vm.start()
    session = vm.wait_for_login()
    old_parts = utils_disk.get_parts_list(session)
    session.close()
    vm.destroy(gracefully=False)

    # Back up xml file.
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    # Additional disk images.
    disks_img = []
    try:
        # Clean up dirty secrets in test environments if there are.
        utils_secret.clean_up_secrets()
        # Setup backend storage
        if backend_storage_type == "file":
            image_filename = params.get("image_filename", "raw.img")
            disk_path = os.path.join(data_dir.get_tmp_dir(), image_filename)
            device_source = libvirt.create_local_disk(backend_storage_type, disk_path, storage_size, device_format)
            disks_img.append({"format": device_format,
                              "source": disk_path, "path": disk_path})
            disk_src_dict = {'attrs': {'file': device_source,
                                       'type_name': 'file'}}
        # Setup backend storage
        elif backend_storage_type == "luks":
            luks_encrypt_passwd = params.get("luks_encrypt_passwd", "password")
            luks_secret_passwd = params.get("luks_secret_passwd", "password")
            # Create secret
            luks_sec_uuid = libvirt.create_secret(params)
            logging.debug("A secret created with uuid = '%s'", luks_sec_uuid)
            virsh.secret_set_value(luks_sec_uuid, luks_secret_passwd,
                                   encode=True, ignore_status=False, debug=True)
            image_filename = params.get("image_filename", "raw.img")
            device_source = os.path.join(data_dir.get_tmp_dir(), image_filename)

            disks_img.append({"format": device_format,
                              "source": device_source, "path": device_source})
            disk_src_dict = {'attrs': {'file': device_source,
                                       'type_name': 'file'}}
            disk_encryption_dict = {"encryption": "luks",
                                    "secret": {"type": "passphrase",
                                               "uuid": luks_sec_uuid}}

            cmd = ("qemu-img create -f luks "
                   "--object secret,id=sec0,data=`printf '%s' | base64`,format=base64 "
                   "-o key-secret=sec0 %s %s" % (luks_encrypt_passwd, device_source, storage_size))
            if process.system(cmd, shell=True):
                test.error("Can't create a luks encrypted img by qemu-img")
        elif backend_storage_type == "iscsi":
            iscsi_host = params.get("iscsi_host")
            iscsi_port = params.get("iscsi_port")
            if device_type == "block":
                device_source = libvirt.setup_or_cleanup_iscsi(is_setup=True)
                disk_src_dict = {'attrs': {'dev': device_source}}
            elif device_type == "network":
                chap_user = params.get("chap_user", "redhat")
                chap_passwd = params.get("chap_passwd", "password")
                auth_sec_usage = params.get("auth_sec_usage",
                                            "libvirtiscsi")
                auth_sec_dict = {"sec_usage": "iscsi",
                                 "sec_target": auth_sec_usage}
                auth_sec_uuid = libvirt.create_secret(auth_sec_dict)
                # Set password of auth secret (not luks encryption secret)
                virsh.secret_set_value(auth_sec_uuid, chap_passwd,
                                       encode=True, ignore_status=False, debug=True)
                iscsi_target, lun_num = libvirt.setup_or_cleanup_iscsi(
                    is_setup=True, is_login=False, image_size=storage_size,
                    chap_user=chap_user, chap_passwd=chap_passwd,
                    portal_ip=iscsi_host)
                # ISCSI auth attributes for disk xml
                disk_auth_dict = {"auth_user": chap_user,
                                  "secret_type": auth_sec_usage_type,
                                  "secret_usage": auth_sec_usage_target}
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
            cmd = ("qemu-img create -f %s "
                   "%s %s" % (device_format, device_source, storage_size))
            if process.system(cmd, shell=True):
                test.error("Can't create a gluster type img by qemu-img")
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

            key_file = os.path.join(data_dir.get_tmp_dir(), "ceph.key")
            key_opt = ""
            # Prepare a blank params to confirm if delete the configure at the end of the test
            ceph_cfg = ""
            if not utils_package.package_install(["ceph-common"]):
                test.error("Failed to install ceph-common")
            # Create config file if it doesn't exist
            ceph_cfg = ceph.create_config_file(ceph_mon_ip)
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
            cmd = ("rbd -m {0} {1} info {2} && rbd -m {0} {1} rm "
                   "{2}".format(ceph_mon_ip, key_opt, ceph_disk_name))
            cmd_result = process.run(cmd, ignore_status=True, shell=True)
            logging.debug("pre clean up rbd disk if exists: %s", cmd_result)
            # Create an local image and make FS on it.
            disk_cmd = ("qemu-img create -f %s %s %s" %
                        (device_format, img_file, storage_size))
            process.run(disk_cmd, ignore_status=False, shell=True)
            # Convert the image to remote storage
            disk_path = ("rbd:%s:mon_host=%s" %
                         (ceph_disk_name, ceph_mon_ip))
            if ceph_client_name and ceph_client_key:
                disk_path += (":id=%s:key=%s" %
                              (ceph_auth_user, ceph_auth_key))
            rbd_cmd = ("rbd -m %s %s info %s 2> /dev/null|| qemu-img convert -O"
                       " %s %s %s" % (ceph_mon_ip, key_opt, ceph_disk_name,
                                      device_format, img_file, disk_path))
            process.run(rbd_cmd, ignore_status=False, shell=True)
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
            tmp_dir = data_dir.get_tmp_dir()
            pvt = libvirt.PoolVolumeTest(test, params)
            pvt.pre_pool(pool_name, pool_type, pool_target, emulated_image)
            # Set virt_use_nfs
            virt_use_nfs = params.get("virt_use_nfs", "off")
            result = process.run("setsebool virt_use_nfs %s" % virt_use_nfs, shell=True)
            if result.exit_status:
                test.error("Failed to set virt_use_nfs value")

            nfs_mount_dir = os.path.join(tmp_dir, pool_target)
            device_source = nfs_mount_dir + image_name
            # Create one image on nfs server
            libvirt.create_local_disk("file", device_source, '1', "raw")
            disks_img.append({"format": device_format,
                              "source": device_source, "path": device_source})
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
            volume_target_encypt = params.get("target_encypt", "")
            volume_target_label = params.get("target_label")
            vol_params = {"name": volume_name, "capacity": int(volume_cap),
                          "allocation": int(volume_alloc), "format":
                          volume_target_format, "path": volume_target_path,
                          "label": volume_target_label,
                          "capacity_unit": volume_cap_unit}
            try:
                # If Libvirt version is lower than 2.5.0
                # Creating luks encryption volume is not supported,so skip it.
                create_vol(pool_name, vol_params)
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
        elif backend_storage_type == "nbd":
            # Get server hostname.
            hostname = process.run('hostname', ignore_status=False, shell=True, verbose=True).stdout_text.strip()
            # Setup backend storage
            nbd_server_host = hostname
            nbd_server_port = params.get("nbd_server_port")
            image_path = params.get("emulated_image", "/var/lib/libvirt/images/nbdtest.img")
            # Create NbdExport object
            nbd = NbdExport(image_path, image_format=device_format,
                            port=nbd_server_port)
            nbd.start_nbd_server()
            # Prepare disk source xml
            source_attrs_dict = {"protocol": "nbd"}
            disk_src_dict = {}
            disk_src_dict.update({"attrs": source_attrs_dict})
            disk_src_dict.update({"hosts": [{"name": nbd_server_host, "port": nbd_server_port}]})
            device_source = "nbd://%s:%s/%s" % (nbd_server_host,
                                                nbd_server_port,
                                                image_path)

        logging.debug("device source is: %s", device_source)

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
            disk_xml.auth = disk_xml.new_auth(**disk_auth_dict)
        if disk_encryption_dict:
            disk_encryption_dict = {"encryption": "luks",
                                    "secret": {"type": "passphrase",
                                               "uuid": luks_sec_uuid}}
            disk_encryption = disk_xml.new_encryption(**disk_encryption_dict)

            disk_xml.encryption = disk_encryption
        disk_xml.source = disk_source
        logging.debug("new disk xml is: %s", disk_xml)
        # Sync VM xml except mirror_mode_blockcommit or mirror_mode_blockcopy
        if (not mirror_mode_blockcommit and not mirror_mode_blockcopy):
            vmxml.add_device(disk_xml)
        try:
            vmxml.sync()
            vm.start()
            vm.wait_for_login().close()
        except xcepts.LibvirtXMLError as xml_error:
            if not define_error:
                test.fail("Failed to define VM:\n%s", str(xml_error))
        except virt_vm.VMStartError as details:
            # When use wrong password in disk xml for cold plug cases,
            # VM cannot be started
            if status_error:
                logging.info("VM failed to start as expected: %s", str(details))
            else:
                test.fail("VM should start but failed: %s" % str(details))
        func_name = trigger_block_threshold_event
        # Additional operations before set block threshold
        if backend_storage_type == "file":
            logging.info("Create snapshot...")
            snap_opt = " %s --disk-only "
            snap_opt += "%s,snapshot=external,file=%s"
            if default_snapshot_test:
                for index in range(1, 5):
                    snapshot_name = "snapshot_%s" % index
                    snap_path = "%s/%s_%s.snap" % (tmp_dir, vm_name, index)
                    snapshot_external_disks.append(snap_path)
                    snap_option = snap_opt % (snapshot_name, device_target, snap_path)
                    virsh.snapshot_create_as(vm_name, snap_option,
                                             ignore_status=False, debug=True)

            if mirror_mode_blockcommit:
                if not libvirt_version.version_compare(6, 6, 0):
                    test.cancel("Set threshold for disk mirroring feature is not supported on current version")
                vmxml.del_device(disk_xml)
                virsh.snapshot_create_as(vm_name, "--disk-only --no-metadata",
                                         ignore_status=False, debug=True)
                # Do active blockcommit in background.
                blockcommit_options = "--active"
                mirror_blockcommit_thread = threading.Thread(target=trigger_block_commit,
                                                             args=(vm_name, 'vda', blockcommit_options,),
                                                             kwargs={'debug': True})
                mirror_blockcommit_thread.start()
                device_target = "vda[1]"
                func_name = trigger_mirror_threshold_event
            if mirror_mode_blockcopy:
                if not libvirt_version.version_compare(6, 6, 0):
                    test.cancel("Set threshold for disk mirroring feature is not supported on current version")
                # Do transient blockcopy in backgroud.
                blockcopy_options = "--transient-job "
                # Do cleanup
                if os.path.exists(dest_path):
                    libvirt.delete_local_disk("file", dest_path)
                mirror_blockcopy_thread = threading.Thread(target=trigger_block_copy,
                                                           args=(vm_name, 'vda', dest_path, blockcopy_options,),
                                                           kwargs={'debug': True})
                mirror_blockcopy_thread.start()
                mirror_blockcopy_thread.join(10)
                device_target = "vda[%d]" % get_mirror_source_index(vm_name)
                func_name = trigger_mirror_threshold_event
        set_vm_block_domblkthreshold(vm_name, device_target, block_threshold_value, **{"debug": True})
        cli_thread = threading.Thread(target=func_name,
                                      args=(vm, device_target))
        cli_thread.start()
        check_threshold_event(vm_name, event_type, block_threshold_timeout, block_threshold_option, **{"debug": True})
    finally:
        # Delete snapshots.
        if virsh.domain_exists(vm_name):
            #To delete snapshot, destroy VM first.
            if vm.is_alive():
                vm.destroy()
            libvirt.clean_up_snapshots(vm_name, domxml=vmxml_backup)

        vmxml_backup.sync("--snapshots-metadata")

        if os.path.exists(img_file):
            libvirt.delete_local_disk("file", img_file)
        for img in disks_img:
            if os.path.exists(img["path"]):
                libvirt.delete_local_disk("file", img["path"])

        for disk in snapshot_external_disks:
            libvirt.delete_local_disk('file', disk)

        if os.path.exists(dest_path):
            libvirt.delete_local_disk("file", dest_path)

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
        elif backend_storage_type == "nfs":
            result = process.run("setsebool virt_use_nfs off",
                                 shell=True)
            if result.exit_status:
                logging.info("Failed to restore virt_use_nfs value")
        elif backend_storage_type == "nbd":
            if nbd:
                try:
                    nbd.cleanup()
                except Exception as ndbEx:
                    logging.info("Clean Up nbd failed: %s" % str(ndbEx))
        # Clean up secrets
        if auth_sec_uuid:
            virsh.secret_undefine(auth_sec_uuid)
        if luks_sec_uuid:
            virsh.secret_undefine(luks_sec_uuid)

        # Clean up pools
        if pvt:
            pvt.cleanup_pool(pool_name, pool_type, pool_target, emulated_image)
