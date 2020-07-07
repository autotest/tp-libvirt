import os
import logging
import socket
from avocado.utils import process

from virttest import data_dir
from virttest import virt_vm
from virttest import virsh
from virttest import utils_package
from virttest import ceph
from virttest import utils_disk
from virttest import utils_secret

from virttest.utils_test import libvirt
from virttest.utils_nbd import NbdExport

from virttest.libvirt_xml import vm_xml, xcepts
from virttest.libvirt_xml.devices.disk import Disk


def run(test, params, env):
    """
    Test virsh blockcopy --xml option.

    1.Prepare backend storage (file/block/iscsi/ceph/nbd)
    2.Start VM
    3.Prepare target xml
    4.Execute virsh blockcopy --xml command
    5.Check VM xml after operation accomplished
    6.Clean up test environment
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    virsh_dargs = {'debug': True, 'ignore_status': True}
    ignore_check = False

    def check_blockcopy_xml(vm_name, source_image, ignore_check=False):
        """
        Check blockcopy xml in VM.

        :param vm_name: VM name
        :param source_image: source image name.
        :param ignore_check: default is False.
        """
        if ignore_check:
            return
        source_imge_list = []
        blklist = virsh.domblklist(vm_name).stdout_text.splitlines()
        for line in blklist:
            if line.strip().startswith(('hd', 'vd', 'sd', 'xvd')):
                source_imge_list.append(line.split()[-1])
        logging.debug('domblklist %s:\n%s', vm_name, source_imge_list)
        if not any(source_image in s for s in source_imge_list):
            test.fail("Cannot find expected source image: %s" % source_image)

    # Disk specific attributes.
    device = params.get("virt_disk_device", "disk")
    device_target = params.get("virt_disk_device_target", "vdd")
    device_format = params.get("virt_disk_device_format", "raw")
    device_type = params.get("virt_disk_device_type", "file")
    device_bus = params.get("virt_disk_device_bus", "virtio")
    backend_storage_type = params.get("backend_storage_type", "iscsi")
    blockcopy_option = params.get("blockcopy_option")

    # Backend storage auth info
    storage_size = params.get("storage_size", "1G")
    enable_auth = "yes" == params.get("enable_auth")
    use_auth_usage = "yes" == params.get("use_auth_usage")
    auth_sec_usage_type = params.get("auth_sec_usage_type", "iscsi")
    auth_sec_usage_target = params.get("auth_sec_usage_target", "libvirtiscsi")
    auth_sec_uuid = ""
    disk_auth_dict = {}
    size = "1"

    status_error = "yes" == params.get("status_error")
    define_error = "yes" == params.get("define_error")

    # Initialize one NbdExport object
    nbd = None
    img_file = os.path.join(data_dir.get_tmp_dir(),
                            "%s_test.img" % vm_name)
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
            if blockcopy_option in ['reuse_external']:
                device_source = libvirt.create_local_disk(backend_storage_type, disk_path, storage_size, device_format)
            else:
                device_source = disk_path
            disks_img.append({"format": device_format,
                              "source": disk_path, "path": disk_path})
            disk_src_dict = {'attrs': {'file': device_source,
                                       'type_name': 'file'}}
            checkout_device_source = image_filename
        elif backend_storage_type == "iscsi":
            iscsi_host = params.get("iscsi_host")
            iscsi_port = params.get("iscsi_port")
            if device_type == "block":
                device_source = libvirt.setup_or_cleanup_iscsi(is_setup=True)
                disk_src_dict = {'attrs': {'dev': device_source}}
                checkout_device_source = device_source
            elif device_type == "network":
                chap_user = params.get("chap_user", "redhat")
                chap_passwd = params.get("chap_passwd", "password")
                auth_sec_usage = params.get("auth_sec_usage",
                                            "libvirtiscsi")
                auth_sec_dict = {"sec_usage": "iscsi",
                                 "sec_target": auth_sec_usage}
                auth_sec_uuid = libvirt.create_secret(auth_sec_dict)
                # Set password of auth secret
                virsh.secret_set_value(auth_sec_uuid, chap_passwd,
                                       encode=True, debug=True)
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
                checkout_device_source = 'emulated-iscsi'
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
            size = "0.15"

            key_file = os.path.join(data_dir.get_tmp_dir(), "ceph.key")
            key_opt = ""
            # Prepare a blank params to confirm whether it needs delete the configure at the end of the test
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
                                       ignore_status=False, debug=True)
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
            if blockcopy_option in ['reuse_external']:
                # Create an local image and make FS on it.
                libvirt.create_local_disk("file", img_file, storage_size, device_format)
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
            checkout_device_source = ceph_disk_name
        elif backend_storage_type == "nbd":
            # Get server hostname.
            hostname = socket.gethostname().strip()
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
            checkout_device_source = image_path
            if blockcopy_option in ['pivot']:
                ignore_check = True

        logging.debug("device source is: %s", device_source)
        # Add disk xml.
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        disk_xml = Disk(type_name=device_type)
        disk_xml.device = device
        disk_xml.target = {"dev": device_target, "bus": device_bus}
        driver_dict = {"name": "qemu", "type": device_format}
        disk_xml.driver = driver_dict
        disk_source = disk_xml.new_disk_source(**disk_src_dict)
        auth_in_source = True
        if disk_auth_dict:
            logging.debug("disk auth dict is: %s" % disk_auth_dict)
            disk_source.auth = disk_xml.new_auth(**disk_auth_dict)
        disk_xml.source = disk_source
        logging.debug("new disk xml is: %s", disk_xml)
        # Sync VM xml
        device_source_path = os.path.join(data_dir.get_tmp_dir(), "source.raw")
        tmp_device_source = libvirt.create_local_disk("file", path=device_source_path,
                                                      size=size, disk_format="raw")
        s_attach = virsh.attach_disk(vm_name, tmp_device_source, device_target,
                                     "--config", debug=True)
        libvirt.check_exit_status(s_attach)
        try:
            vm.start()
            vm.wait_for_login().close()
        except xcepts.LibvirtXMLError as xml_error:
            if not define_error:
                test.fail("Failed to define VM:\n%s", str(xml_error))
        except virt_vm.VMStartError as details:
            # VM cannot be started
            if status_error:
                logging.info("VM failed to start as expected: %s", str(details))
            else:
                test.fail("VM should start but failed: %s" % str(details))
        # Additional operations before set block threshold
        options = params.get("options", "--pivot --transient-job --verbose --wait")
        result = virsh.blockcopy(vm_name, device_target, "--xml %s" % disk_xml.xml,
                                 options=options,
                                 debug=True)
        libvirt.check_exit_status(result)
        check_source_image = None
        if blockcopy_option in ['pivot']:
            check_source_image = checkout_device_source
        else:
            check_source_image = tmp_device_source
        check_blockcopy_xml(vm_name, check_source_image, ignore_check)
    finally:
        # Delete snapshots.
        if virsh.domain_exists(vm_name):
            #To Delet snapshot, destroy vm first.
            if vm.is_alive():
                vm.destroy()
            libvirt.clean_up_snapshots(vm_name, domxml=vmxml_backup)

        vmxml_backup.sync("--snapshots-metadata")

        if os.path.exists(img_file):
            libvirt.delete_local_disk("file", img_file)
        for img in disks_img:
            if os.path.exists(img["path"]):
                libvirt.delete_local_disk("file", img["path"])
        # Clean up backend storage
        if backend_storage_type == "iscsi":
            libvirt.setup_or_cleanup_iscsi(is_setup=False)
        elif backend_storage_type == "ceph":
            # Remove ceph configure file if created.
            if ceph_cfg:
                os.remove(ceph_cfg)
            cmd = ("rbd -m {0} {1} info {2} && rbd -m {0} {1} rm "
                   "{2}".format(ceph_mon_ip, key_opt, ceph_disk_name))
            cmd_result = process.run(cmd, ignore_status=True, shell=True)
            logging.debug("result of rbd removal: %s", cmd_result.stdout_text)
            if os.path.exists(key_file):
                os.remove(key_file)
        elif backend_storage_type == "nbd":
            if nbd:
                try:
                    nbd.cleanup()
                except Exception as ndbEx:
                    logging.error("Clean Up nbd failed: %s" % str(ndbEx))
        # Clean up secrets
        if auth_sec_uuid:
            virsh.secret_undefine(auth_sec_uuid)
