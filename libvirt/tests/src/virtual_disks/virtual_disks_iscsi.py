import os
import re
import locale
import logging
import base64

import aexpect

from avocado.utils import process

from virttest import remote
from virttest import data_dir
from virttest import virt_vm
from virttest import virsh
from virttest import utils_disk
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml, xcepts
from virttest.libvirt_xml import secret_xml
from virttest.libvirt_xml.devices.disk import Disk
from virttest.libvirt_xml.devices.controller import Controller

from virttest import libvirt_version


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
            new_parts = utils_disk.get_parts_list(session)
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
            elif target.startswith("sd"):
                added_part = added_parts[0]
            if not added_part:
                logging.error("Cann't see added partition in VM")
                return False
            utils_disk.linux_disk_check(session, added_part)
            session.close()
            return True

        except (remote.LoginError, virt_vm.VMError, aexpect.ShellError) as e:
            logging.error(str(e))
            return False

    def check_qemu_cmd():
        """
        Check qemu-kvm command line options
        """
        cmd = ("ps -ef | grep %s | grep -v grep " % vm_name)
        if driver_iothread:
            cmd += " | grep iothread=iothread%s" % driver_iothread

        if process.system(cmd, ignore_status=True, shell=True):
            test.fail("Can't see disk option '%s' "
                      "in command line" % cmd)

    def check_auth_plaintext(vm_name, password):
        """
        Check if libvirt passed the plaintext of the chap authentication
        password to qemu.
        :param vm_name: The name of vm to be checked.
        :param password: The plaintext of password used for chap authentication.
        :return: True if using plaintext, False if not.
        """
        cmd = ("ps -ef | grep -v grep | grep qemu-kvm | grep %s | grep %s"
               % (vm_name, password))
        return process.system(cmd, ignore_status=True, shell=True) == 0

    # Disk specific attributes.
    device = params.get("virt_disk_device", "disk")
    device_target = params.get("virt_disk_device_target", "vdd")
    device_format = params.get("virt_disk_device_format", "raw")
    device_type = params.get("virt_disk_device_type", "file")
    device_bus = params.get("virt_disk_device_bus", "virtio")

    # Controller specific attributes.
    cntlr_type = params.get('controller_type', None)
    cntlr_model = params.get('controller_model', None)
    cntlr_index = params.get('controller_index', None)
    controller_addr_options = params.get('controller_addr_options', None)

    driver_iothread = params.get("driver_iothread")

    # iscsi options.
    iscsi_target = params.get("iscsi_target")
    iscsi_host = params.get("iscsi_host")
    iscsi_port = params.get("iscsi_port")
    emulated_size = params.get("iscsi_image_size", "1")
    uuid = params.get("uuid", "")
    auth_uuid = "yes" == params.get("auth_uuid", "")
    auth_usage = "yes" == params.get("auth_usage", "")

    status_error = "yes" == params.get("status_error")
    define_error = "yes" == params.get("define_error", "no")
    test_save_snapshot = "yes" == params.get("test_save_snapshot", "no")
    test_qemu_cmd = "yes" == params.get("test_qemu_cmd", "no")
    check_partitions = "yes" == params.get("virt_disk_check_partitions", "yes")

    secret_uuid = ""

    # Start vm and get all partions in vm.
    if device == "lun":
        if vm.is_dead():
            vm.start()
        session = vm.wait_for_login()
        old_parts = utils_disk.get_parts_list(session)
        session.close()
        vm.destroy(gracefully=False)

    # Back up xml file.
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    try:
        chap_user = ""
        chap_passwd = ""
        if auth_uuid or auth_usage:
            auth_place_in_location = params.get("auth_place_in_location")
            if 'source' in auth_place_in_location and not libvirt_version.version_compare(3, 9, 0):
                test.cancel("place auth in source is not supported in current libvirt version")
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
                                     ret.stdout.strip())[0].lstrip()
            logging.debug("Secret uuid %s", secret_uuid)
            if secret_uuid == "":
                test.error("Failed to get secret uuid")

            # Set secret value
            encoding = locale.getpreferredencoding()
            secret_string = base64.b64encode(chap_passwd.encode(encoding)).decode(encoding)
            ret = virsh.secret_set_value(secret_uuid, secret_string,
                                         **virsh_dargs)
            libvirt.check_exit_status(ret)

        # Setup iscsi target
        iscsi_target, lun_num = libvirt.setup_or_cleanup_iscsi(is_setup=True,
                                                               is_login=False,
                                                               image_size=emulated_size,
                                                               chap_user=chap_user,
                                                               chap_passwd=chap_passwd,
                                                               portal_ip=iscsi_host)

        # If we use qcow2 disk format, should format iscsi disk first.
        if device_format == "qcow2":
            cmd = ("qemu-img create -f qcow2 iscsi://%s:%s/%s/%s %s"
                   % (iscsi_host, iscsi_port, iscsi_target, lun_num, emulated_size))
            process.run(cmd, shell=True)

        # Add disk xml.
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)

        disk_xml = Disk(type_name=device_type)
        disk_xml.device = device

        disk_xml.target = {"dev": device_target, "bus": device_bus}
        driver_dict = {"name": "qemu", "type": device_format}

        # For lun type device, iothread attribute need to be set in controller.
        if driver_iothread and device != "lun":
            driver_dict.update({"iothread": driver_iothread})
            vmxml.iothreads = int(driver_iothread)
        elif driver_iothread:
            vmxml.iothreads = int(driver_iothread)

        disk_xml.driver = driver_dict
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
        disk_source = disk_xml.new_disk_source(
            **{"attrs": {"protocol": "iscsi",
                         "name": "%s/%s" % (iscsi_target, lun_num)},
               "hosts": [{"name": iscsi_host, "port": iscsi_port}]})
        if auth_dict:
            disk_auth = disk_xml.new_auth(**auth_dict)
            if 'source' in auth_place_in_location:
                disk_source.auth = disk_auth
            if 'disk' in auth_place_in_location:
                disk_xml.auth = disk_auth

        disk_xml.source = disk_source
        if device != "lun":
            device_str = "serial_" + device_target
            disk_xml.serial = device_str

        # Sync VM xml.
        vmxml.add_device(disk_xml)

        # After virtio 1.0 is enabled, lun type device need use virtio-scsi
        # instead of virtio, so additional controller is needed.
        # Add controller.
        if device == "lun":
            ctrl = Controller(type_name=cntlr_type)
            if cntlr_model is not None:
                ctrl.model = cntlr_model
            if cntlr_index is not None:
                ctrl.index = cntlr_index
            if controller_addr_options:
                ctrl_addr_dict = {}
                for addr_option in controller_addr_options.split(','):
                    if addr_option != "":
                        addr_part = addr_option.split('=')
                        ctrl_addr_dict.update({addr_part[0].strip(): addr_part[1].strip()})
                ctrl.address = ctrl.new_controller_address(attrs=ctrl_addr_dict)

            # If driver_iothread is true, need add iothread attribute in controller.
            if driver_iothread:
                ctrl_driver_dict = {}
                ctrl_driver_dict.update({"iothread": driver_iothread})
                ctrl.driver = ctrl_driver_dict
            logging.debug("Controller XML is:%s", ctrl)
            if cntlr_type:
                vmxml.del_controller(cntlr_type)
            else:
                vmxml.del_controller("scsi")
            vmxml.add_device(ctrl)

        try:
            # Start the VM and check status.
            vmxml.sync()
            vm.start()
            if status_error:
                test.fail("VM started unexpectedly.")

            # Check Qemu command line
            if test_qemu_cmd:
                check_qemu_cmd()

        except virt_vm.VMStartError as e:
            if status_error:
                if re.search(uuid, str(e)):
                    pass
            else:
                test.fail("VM failed to start."
                          "Error: %s" % str(e))
        except xcepts.LibvirtXMLError as xml_error:
            if not define_error:
                test.fail("Failed to define VM:\n%s" % xml_error)
        else:
            # Check partitions in VM.
            if check_partitions:
                if device == "lun":
                    if not check_in_vm(device_target, old_parts):
                        test.fail("Check disk partitions in VM failed")
                else:
                    session = vm.wait_for_login()
                    added_part = utils_disk.get_disk_by_serial(device_str, session=session)
                    if not added_part:
                        test.fail("Unable to get disk with serial {}".format(device_str))
                    utils_disk.linux_disk_check(session, added_part)
                    session.close()

            # Test domain save/restore/snapshot.
            if test_save_snapshot:
                save_file = os.path.join(data_dir.get_data_dir(), "%.save" % vm_name)
                check_save_restore(save_file)
                check_snapshot()
                if os.path.exists(save_file):
                    os.remove(save_file)
            # Test libvirt doesn't pass the plaintext of chap password to qemu,
            # this function is implemented in libvirt 4.3.0-1.
            if (libvirt_version.version_compare(4, 3, 0) and
                    (auth_uuid or auth_usage) and
                    chap_passwd):
                if(check_auth_plaintext(vm_name, chap_passwd)):
                    test.fail("Libvirt should not pass plaintext of chap "
                              "password to qemu-kvm.")

    finally:
        # Close session.
        if 'session' in locals():
            session.close()

        # Delete snapshots.
        libvirt.clean_up_snapshots(vm_name, domxml=vmxml_backup)

        # Recover VM.
        if vm.is_alive():
            vm.destroy(gracefully=False)
        vmxml_backup.sync("--snapshots-metadata")

        # Delete the tmp files.
        libvirt.setup_or_cleanup_iscsi(is_setup=False)

        # Clean up secret
        if secret_uuid:
            virsh.secret_undefine(secret_uuid)
