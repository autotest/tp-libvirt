import os
import re
import logging
import base64
from virttest.utils_test import libvirt
from autotest.client.shared import error
from autotest.client import utils
from virttest import aexpect
from virttest import remote
from virttest import virt_vm
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml import secret_xml
from virttest.libvirt_xml.devices.disk import Disk


def run(test, params, env):
    """
    Test disk encryption option.

    1.Prepare test environment, destroy or suspend a VM.
    2.Prepare tgtd and secret config.
    3.Edit disks xml and start the domain.
    4.Perform test operation.
    5.Recover test environment.
    6.Confirm the test result.
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    virsh_dargs = {'debug': True, 'ignore_status': True}

    def check_save_restore(save_file):
        """
        Test domain save and restore.
        """
        # Save the domain.
        ret = virsh.save(vm_name, save_file, **virsh_dargs)
        libvirt.check_exit_status(ret)

        # Restore the domain.
        ret = virsh.restore(save_file, **virsh_dargs)
        libvirt.check_exit_status(ret)

    def check_snapshot():
        """
        Test domain snapshot operation.
        """
        snapshot1 = "s1"
        snapshot2 = "s2"

        ret = virsh.snapshot_create_as(vm_name, snapshot1)
        libvirt.check_exit_status(ret)

        ret = virsh.snapshot_create_as(vm_name,
                                       "%s --disk-only --diskspec vda,"
                                       "file=/tmp/testvm-snap1"
                                       % snapshot2)
        libvirt.check_exit_status(ret, True)

        ret = virsh.snapshot_create_as(vm_name,
                                       "%s --memspec file=%s,snapshot=external"
                                       " --diskspec vda,file=/tmp/testvm-snap2"
                                       % (snapshot2, snapshot2))
        libvirt.check_exit_status(ret, True)

    def check_in_vm(target, old_parts):
        """
        Check mount/read/write disk in VM.
        :param vm. VM guest.
        :param target. Disk dev in VM.
        :return: True if check successfully.
        """
        try:
            session = vm.wait_for_login()
            new_parts = libvirt.get_parts_list(session)
            added_parts = list(set(new_parts).difference(set(old_parts)))
            logging.info("Added parts:%s", added_parts)
            if len(added_parts) != 1:
                logging.error("The number of new partitions is invalid in VM")
                return False

            added_part = None
            if target.startswith("vd"):
                if added_parts[0].startswith("vd"):
                    added_part = added_parts[0]
            elif target.startswith("hd"):
                if added_parts[0].startswith("sd"):
                    added_part = added_parts[0]

            if not added_part:
                logging.error("Cann't see added partition in VM")
                return False

            cmd = ("fdisk -l /dev/{0} && mkfs.ext3 -F /dev/{0} && "
                   "mkdir test && mount /dev/{0} test && echo"
                   " teststring > test/testfile && umount test"
                   .format(added_part))
            s, o = session.cmd_status_output(cmd)
            logging.info("Check disk operation in VM:\n%s", o)
            if s != 0:
                return False
            return True

        except (remote.LoginError, virt_vm.VMError, aexpect.ShellError), e:
            logging.error(str(e))
            return False

    # Disk specific attributes.
    device = params.get("virt_disk_device", "disk")
    device_target = params.get("virt_disk_device_target", "vdd")
    device_format = params.get("virt_disk_device_format", "raw")
    device_type = params.get("virt_disk_device_type", "file")
    device_bus = params.get("virt_disk_device_bus", "virtio")

    # iscsi options.
    iscsi_target = params.get("iscsi_target")
    iscsi_host = params.get("iscsi_host")
    iscsi_port = params.get("iscsi_port")
    emulated_size = params.get("iscsi_image_size", "1")
    uuid = params.get("uuid", "")
    auth_uuid = "yes" == params.get("auth_uuid", "")
    auth_usage = "yes" == params.get("auth_usage", "")

    status_error = "yes" == params.get("status_error")
    test_save_snapshot = "yes" == params.get("test_save_snapshot", "no")
    check_partitions = "yes" == params.get("virt_disk_check_partitions", "yes")

    secret_uuid = ""

    # Start vm and get all partions in vm.
    if vm.is_dead():
        vm.start()
    session = vm.wait_for_login()
    old_parts = libvirt.get_parts_list(session)
    session.close()
    vm.destroy(gracefully=False)

    # Back up xml file.
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    try:
        chap_user = ""
        chap_passwd = ""
        if auth_uuid or auth_usage:
            auth_type = params.get("auth_type")
            secret_usage_target = params.get("secret_usage_target")
            secret_usage_type = params.get("secret_usage_type")
            chap_user = params.get("iscsi_user")
            chap_passwd = params.get("iscsi_password")

            sec_xml = secret_xml.SecretXML("no", "yes")
            sec_xml.description = "iSCSI secret"
            sec_xml.auth_type = auth_type
            sec_xml.auth_username = chap_user
            sec_xml.usage = secret_usage_type
            sec_xml.target = secret_usage_target
            sec_xml.xmltreefile.write()

            ret = virsh.secret_define(sec_xml.xml)
            libvirt.check_exit_status(ret)

            secret_uuid = re.findall(r".+\S+(\ +\S+)\ +.+\S+",
                                     ret.stdout)[0].lstrip()
            logging.debug("Secret uuid %s", secret_uuid)
            if secret_uuid == "":
                raise error.TestNAError("Failed to get secret uuid")

            # Set secret value
            secret_string = base64.b64encode(chap_passwd)
            ret = virsh.secret_set_value(secret_uuid, secret_string,
                                         **virsh_dargs)
            libvirt.check_exit_status(ret)

        # Setup iscsi target
        iscsi_target = libvirt.setup_or_cleanup_iscsi(is_setup=True,
                                                      is_login=False,
                                                      image_size=emulated_size,
                                                      chap_user=chap_user,
                                                      chap_passwd=chap_passwd)

        # If we use qcow2 disk format, should format iscsi disk first.
        if device_format == "qcow2":
            cmd = ("qemu-img create -f qcow2 iscsi://%s:%s/%s/1 %s"
                   % (iscsi_host, iscsi_port, iscsi_target, emulated_size))
            utils.run(cmd)

        # Add disk xml.
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)

        disk_xml = Disk(type_name=device_type)
        disk_xml.device = device
        disk_xml.source = disk_xml.new_disk_source(
            **{"attrs": {"protocol": "iscsi", "name": "%s/1" % iscsi_target},
               "hosts": [{"name": iscsi_host, "port": iscsi_port}]})
        disk_xml.target = {"dev": device_target, "bus": device_bus}
        disk_xml.driver = {"name": "qemu", "type": device_format}

        # Check if we want to use a faked uuid.
        if not uuid:
            uuid = secret_uuid
        auth_dict = {}
        if auth_uuid:
            auth_dict = {"auth_user": chap_user,
                         "secret_type": secret_usage_type,
                         "secret_uuid": uuid}
        elif auth_usage:
            auth_dict = {"auth_user": chap_user,
                         "secret_type": secret_usage_type,
                         "secret_usage": secret_usage_target}
        if auth_dict:
            disk_xml.auth = disk_xml.new_auth(**auth_dict)
        # Sync VM xml.
        vmxml.add_device(disk_xml)
        vmxml.sync()

        try:
            # Start the VM and check status.
            vm.start()
            if status_error:
                raise error.TestFail("VM started unexpectedly.")

        except virt_vm.VMStartError, e:
            if status_error:
                if re.search(uuid, str(e)):
                    pass
            else:
                raise error.TestFail("VM failed to start")
        else:
            # Check partitions in VM.
            if check_partitions:
                if not check_in_vm(device_target, old_parts):
                    raise error.TestFail("Check disk partitions in VM failed")
            # Test domain save/restore/snapshot.
            if test_save_snapshot:
                save_file = os.path.join(test.tmpdir, "%.save" % vm_name)
                check_save_restore(save_file)
                check_snapshot()
                if os.path.exists(save_file):
                    os.remove(save_file)

    finally:
        # Delete snapshots.
        snapshot_lists = virsh.snapshot_list(vm_name)
        if len(snapshot_lists) > 0:
            libvirt.clean_up_snapshots(vm_name, snapshot_lists)
            for snapshot in snapshot_lists:
                virsh.snapshot_delete(vm_name, snapshot, "--metadata")

        # Recover VM.
        vmxml_backup.sync("--snapshots-metadata")

        # Delete the tmp files.
        libvirt.setup_or_cleanup_iscsi(is_setup=False)

        # Clean up secret
        if secret_uuid:
            virsh.secret_undefine(secret_uuid)
