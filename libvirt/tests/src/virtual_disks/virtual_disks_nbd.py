import os
import logging
import aexpect
import platform
import time

from avocado.utils import process

from virttest import remote
from virttest import virt_vm
from virttest import virsh
from virttest import utils_disk
from virttest import utils_secret
from virttest import libvirt_version
from virttest.utils_test import libvirt
from virttest.utils_nbd import NbdExport

from virttest.libvirt_xml import vm_xml, xcepts
from virttest.libvirt_xml.devices.disk import Disk


def run(test, params, env):
    """
    Test nbd disk option.

    1.Prepare backend storage
    2.Use nbd to export the backend storage with or without TLS
    3.Prepare a disk xml indicating to the backend storage
    4.Start VM with disk hotplug/coldplug
    5.Start snapshot or save/restore operations on ndb disk
    6.Check some behaviours on VM
    7.Recover test environment
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    virsh_dargs = {'debug': True, 'ignore_status': False}

    def check_disk_save_restore(save_file):
        """
        Check domain save and restore operation.

        :param save_file: the path to saved file
        """
        # Save the domain.
        ret = virsh.save(vm_name, save_file,
                         **virsh_dargs)
        libvirt.check_exit_status(ret)

        # Restore the domain.
        ret = virsh.restore(save_file, **virsh_dargs)
        libvirt.check_exit_status(ret)

    def check_snapshot():
        """
        Check domain snapshot operations.
        """
        # Cleaup dirty data if exists
        if os.path.exists(snapshot_name1_file):
            os.remove(snapshot_name1_file)
        if os.path.exists(snapshot_name2_mem_file):
            os.remove(snapshot_name2_mem_file)
        if os.path.exists(snapshot_name2_disk_file):
            os.remove(snapshot_name2_disk_file)
        device_target = 'vda'
        snapshot_name1_option = "--diskspec %s,file=%s,snapshot=external --disk-only --atomic" % (device_target, snapshot_name1_file)
        ret = virsh.snapshot_create_as(vm_name, "%s %s" % (snapshot_name1, snapshot_name1_option), debug=True)
        libvirt.check_exit_status(ret)
        snap_lists = virsh.snapshot_list(vm_name, debug=True)
        if snapshot_name1 not in snap_lists:
            test.fail("Snapshot %s doesn't exist"
                      % snapshot_name1)
        # Check file can be created after snapshot

        def _check_file_create(filename):
            """
            Check whether file with specified filename exists or not.

            :param filename: finename
            """
            try:
                session = vm.wait_for_login()
                if platform.platform().count('ppc64'):
                    time.sleep(10)
                cmd = ("echo"
                       " teststring > /tmp/{0}".format(filename))
                status, output = session.cmd_status_output(cmd)
                if status != 0:
                    test.fail("Failed to touch one file on VM internal")
            except (remote.LoginError, virt_vm.VMError, aexpect.ShellError) as e:
                logging.error(str(e))
                raise
            finally:
                if session:
                    session.close()

        _check_file_create("disk.txt")
        # Create memory snapshot.
        snapshot_name2_mem_option = "--memspec file=%s,snapshot=external" % (snapshot_name2_mem_file)
        snapshot_name2_disk_option = "--diskspec %s,file=%s,snapshot=external --atomic" % (device_target, snapshot_name2_disk_file)
        snapshot_name2_option = "%s %s" % (snapshot_name2_mem_option, snapshot_name2_disk_option)
        ret = virsh.snapshot_create_as(vm_name, "%s %s" % (snapshot_name2, snapshot_name2_option), debug=True)
        libvirt.check_exit_status(ret)
        snap_lists = virsh.snapshot_list(vm_name, debug=True)
        if snapshot_name2 not in snap_lists:
            test.fail("Snapshot: %s doesn't exist"
                      % snapshot_name2)
        _check_file_create("mem.txt")

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

    # Disk specific attributes.
    device = params.get("virt_disk_device", "disk")
    device_target = params.get("virt_disk_device_target", "vdb")
    device_format = params.get("virt_disk_device_format", "raw")
    device_type = params.get("virt_disk_device_type", "file")
    device_bus = params.get("virt_disk_device_bus", "virtio")
    backend_storage_type = params.get("backend_storage_type", "iscsi")
    image_path = params.get("emulated_image")
    # Get config parameters
    status_error = "yes" == params.get("status_error")
    define_error = "yes" == params.get("define_error")
    check_partitions = "yes" == params.get("virt_disk_check_partitions", "yes")
    hotplug_disk = "yes" == params.get("hotplug_disk", "no")
    tls_enabled = "yes" == params.get("enable_tls", "no")
    enable_private_key_encryption = "yes" == params.get("enable_private_key_encryption", "no")
    private_key_encrypt_passphrase = params.get("private_key_password")
    domain_operation = params.get("domain_operation")
    secret_uuid = None

    # Get snapshot attributes.
    snapshot_name1 = params.get("snapshot_name1")
    snapshot_name1_file = params.get("snapshot_name1_file")
    snapshot_name2 = params.get("snapshot_name2")
    snapshot_name2_mem_file = params.get("snapshot_name2_mem_file")
    snapshot_name2_disk_file = params.get("snapshot_name2_disk_file")
    # Initialize one NbdExport object
    nbd = None

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
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        # Get server hostname.
        hostname = process.run('hostname', ignore_status=False, shell=True, verbose=True).stdout_text.strip()
        # Setup backend storage
        nbd_server_host = hostname
        nbd_server_port = params.get("nbd_server_port")
        image_path = params.get("emulated_image", "/var/lib/libvirt/images/nbdtest.img")
        export_name = params.get("export_name", None)
        deleteExisted = "yes" == params.get("deleteExisted", "yes")
        tls_bit = "no"
        if tls_enabled:
            tls_bit = "yes"

        # Create secret
        if enable_private_key_encryption:
            # this feature is enabled after libvirt 6.6.0
            if not libvirt_version.version_compare(6, 6, 0):
                test.cancel("current libvirt version doesn't support client private key encryption")
            utils_secret.clean_up_secrets()
            private_key_sec_uuid = libvirt.create_secret(params)
            logging.debug("A secret created with uuid = '%s'", private_key_sec_uuid)
            private_key_sec_passwd = params.get("private_key_password", "redhat")
            ret = virsh.secret_set_value(private_key_sec_uuid, private_key_sec_passwd,
                                         encode=True, use_file=True, debug=True)
            libvirt.check_exit_status(ret)
            secret_uuid = private_key_sec_uuid

        # Initialize special test environment config for snapshot operations.
        if domain_operation == "snap_shot":
            first_disk = vm.get_first_disk_devices()
            image_path = first_disk['source']
            device_target = 'vda'
            # Remove previous xml
            disks = vmxml.get_devices(device_type="disk")
            for disk_ in disks:
                if disk_.target['dev'] == device_target:
                    vmxml.del_device(disk_)
                    break

        # Create NbdExport object
        nbd = NbdExport(image_path, image_format=device_format,
                        port=nbd_server_port, export_name=export_name,
                        tls=tls_enabled, deleteExisted=deleteExisted,
                        private_key_encrypt_passphrase=private_key_encrypt_passphrase, secret_uuid=secret_uuid)
        nbd.start_nbd_server()
        # Prepare disk source xml
        source_attrs_dict = {"protocol": "nbd", "tls": "%s" % tls_bit}
        if export_name:
            source_attrs_dict.update({"name": "%s" % export_name})
        disk_src_dict = {}
        disk_src_dict.update({"attrs": source_attrs_dict})
        disk_src_dict.update({"hosts": [{"name": nbd_server_host, "port": nbd_server_port}]})

        # Add disk xml.
        disk_xml = Disk(type_name=device_type)
        disk_xml.device = device
        disk_xml.target = {"dev": device_target, "bus": device_bus}
        driver_dict = {"name": "qemu", "type": 'raw'}
        disk_xml.driver = driver_dict
        disk_source = disk_xml.new_disk_source(**disk_src_dict)
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
        # Hotplug disk.
        if hotplug_disk:
            result = virsh.attach_device(vm_name, disk_xml.xml,
                                         ignore_status=True, debug=True)
            libvirt.check_exit_status(result, status_error)
        # Check save and restore operation and its result
        if domain_operation == 'save_restore':
            save_file = "/tmp/%s.save" % vm_name
            check_disk_save_restore(save_file)

        # Check attached nbd disk
        if check_partitions and not status_error:
            logging.debug("wait seconds for starting in checking vm part")
            time.sleep(2)
            if not check_in_vm(device_target, old_parts):
                test.fail("Check disk partitions in VM failed")
        # Check snapshot operation and its result
        if domain_operation == 'snap_shot':
            check_snapshot()

        # Unplug disk.
        if hotplug_disk:
            result = virsh.detach_device(vm_name, disk_xml.xml,
                                         ignore_status=True, debug=True, wait_remove_event=True)
            libvirt.check_exit_status(result, status_error)
    finally:
        if enable_private_key_encryption:
            utils_secret.clean_up_secrets()
        # Clean up backend storage and TLS
        try:
            if nbd:
                nbd.cleanup()
            # Clean up snapshots if exist
            if domain_operation == 'snap_shot':
                snap_lists = virsh.snapshot_list(vm_name, debug=True)
                for snap_name in snap_lists:
                    virsh.snapshot_delete(vm_name, snap_name, "--metadata",
                                          debug=True, ignore_status=True)
                # Cleaup dirty data if exists
                if os.path.exists(snapshot_name1_file):
                    os.remove(snapshot_name1_file)
                if os.path.exists(snapshot_name2_mem_file):
                    os.remove(snapshot_name2_mem_file)
                if os.path.exists(snapshot_name2_disk_file):
                    os.remove(snapshot_name2_disk_file)
        except Exception as ndbEx:
            logging.info("Clean Up nbd failed: %s" % str(ndbEx))

        # Recover VM.
        if vm.is_alive():
            vm.destroy(gracefully=False)
        vmxml_backup.sync("--snapshots-metadata")
