import logging
import os
import os.path
import re
import base64
import locale

import aexpect

from avocado.utils import process

from virttest import remote
from virttest import virt_vm
from virttest import virsh
from virttest import utils_disk
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml import secret_xml
from virttest.libvirt_xml.devices.disk import Disk

from virttest import libvirt_version


def run(test, params, env):
    """
    Test disk encryption option.

    1.Prepare test environment,destroy or suspend a VM.
    2.Prepare test image.
    3.Edit disks xml and start the domain.
    4.Perform test operation.
    5.Recover test environment.
    6.Confirm the test result.
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    virsh_dargs = {'debug': True, 'ignore_status': True}

    def create_secret(image_path):
        """
        Create secret.

        :param image_path. Image path.
        :return: secret id if create successfully.
        """
        sec_xml = secret_xml.SecretXML("no", "yes")
        sec_xml.description = "image secret"

        sec_xml.usage = 'volume'
        sec_xml.volume = image_path
        sec_xml.xmltreefile.write()

        ret = virsh.secret_define(sec_xml.xml)
        libvirt.check_exit_status(ret)
        # Get secret uuid.
        try:
            encryption_uuid = re.findall(r".+\S+(\ +\S+)\ +.+\S+",
                                         ret.stdout.strip())[0].lstrip()
        except IndexError as e:
            test.error("Fail to get newly created secret uuid")
        logging.debug("Secret uuid %s", encryption_uuid)

        # Set secret value.
        encoding = locale.getpreferredencoding()
        secret_string = base64.b64encode(secret_password_no_encoded.encode(encoding)).decode(encoding)
        ret = virsh.secret_set_value(encryption_uuid, secret_string,
                                     **virsh_dargs)
        libvirt.check_exit_status(ret)
        return encryption_uuid

    def get_secret_list():
        """
        Get secret list.

        :return: secret list
        """
        logging.info("Get secret list ...")
        secret_list = virsh.secret_list().stdout.strip().splitlines()
        # First two lines contain table header followed by entries
        # for each secret, such as:
        #
        # UUID                                  Usage
        # --------------------------------------------------------------------------------
        # b4e8f6d3-100c-4e71-9f91-069f89742273  ceph client.libvirt secret
        secret_list = secret_list[2:]
        result = []
        # If secret list is not empty.
        if secret_list:
            for line in secret_list:
                # Split on whitespace, assume 1 column
                linesplit = line.split(None, 1)
                result.append(linesplit[0])
        return result

    def check_in_vm(vm, target, old_parts):
        """
        Check mount/read/write disk in VM.
        :param vm. VM guest.
        :param target. Disk dev in VM.
        :return: True if check successfully.
        """
        try:
            session = vm.wait_for_login()
            rpm_stat = session.cmd_status("rpm -q parted || "
                                          "yum install -y parted", 300)
            if rpm_stat != 0:
                test.fail("Failed to query/install parted, make sure"
                          " that you have usable repo in guest")

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

            if not added_part:
                logging.error("Can't see added partition in VM")
                return False

            device_source = os.path.join(os.sep, 'dev', added_part)
            libvirt.mk_label(device_source, session=session)
            libvirt.mk_part(device_source, size="10M", session=session)
            # Run partprobe to make the change take effect.
            process.run("partprobe", ignore_status=True, shell=True)
            libvirt.mkfs("/dev/%s1" % added_part, "ext3", session=session)

            cmd = ("mount /dev/%s1 /mnt && echo '123' > /mnt/testfile"
                   " && cat /mnt/testfile && umount /mnt" % added_part)
            s, o = session.cmd_status_output(cmd)
            logging.info("Check disk operation in VM:\n%s", o)
            session.close()
            if s != 0:
                return False
            return True
        except (remote.LoginError, virt_vm.VMError, aexpect.ShellError) as e:
            logging.error(str(e))
            return False

    # Disk specific attributes.
    device = params.get("virt_disk_device", "disk")
    device_target = params.get("virt_disk_device_target", "vdd")
    device_type = params.get("virt_disk_device_type", "file")
    device_bus = params.get("virt_disk_device_bus", "virtio")
    encryption_in_source = "yes" == params.get("encryption_in_source")
    encryption_out_source = "yes" == params.get("encryption_out_source")
    if encryption_in_source and not libvirt_version.version_compare(3, 9, 0):
        test.cancel("Cannot put <encryption> inside disk <source> in "
                    "this libvirt version.")
    # Image options.
    image_cap = params.get("image_cap")
    image_target_path = params.get("target_path")
    image_target_format = params.get("target_format")
    image_target_encypt = params.get("target_encypt", "")

    hotplug = "yes" == params.get("virt_disk_device_hotplug")
    status_error = "yes" == params.get("status_error")
    secret_type = params.get("secret_type", "passphrase")
    secret_password_no_encoded = params.get("secret_password_no_encoded", "redhat")
    extra_parameter_qcow2 = params.get("extra_parameter_qcow2")
    extra_parameter_raw = params.get("extra_parameter_raw")
    extra_luks_parameter = params.get("extra_parameter")
    slice_test = params.get("slice_test", "yes")

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    # Start vm and get all partions in vm.
    if vm.is_dead():
        vm.start()
    session = vm.wait_for_login()
    old_parts = utils_disk.get_parts_list(session)
    session.close()
    vm.destroy(gracefully=False)

    # Back up xml file.
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    sec_encryption_uuid = None
    try:
        # Prepare the disk.
        sec_uuids = []
        # Clean up dirty secrets in test environments if there are.
        dirty_secret_list = get_secret_list()
        if dirty_secret_list:
            for dirty_secret_uuid in dirty_secret_list:
                virsh.secret_undefine(dirty_secret_uuid)
        image_encryption_params = {}
        image_encryption_params.update({"format": image_target_encypt})
        # create secret.
        sec_encryption_uuid = create_secret(image_target_path)
        sec_uuids.append(sec_encryption_uuid)
        image_encryption_params.update({"secret": {"type": secret_type, "uuid": sec_encryption_uuid}})

        # slice attribute need libvirt 6.1.0 forward version
        if slice_test == "yes" and not libvirt_version.version_compare(6, 1, 0):
            test.cancel("slice attribute not supported for this libvirt version")

        #Prepare test image
        if slice_test == "yes" and image_target_format == "qcow2":
            libvirt.create_local_disk(disk_type="file", extra=extra_luks_parameter,
                                      path=image_target_path, size=int(image_cap), disk_format=image_target_format)
        elif image_target_format == "raw":
            image_target_format_raw = "luks"
            libvirt.create_local_disk(disk_type="file", extra=extra_parameter_raw,
                                      path=image_target_path, size=int(image_cap), disk_format=image_target_format_raw)
        else:
            libvirt.create_local_disk(disk_type="file", extra=extra_parameter_qcow2,
                                      path=image_target_path, size=int(image_cap), disk_format=image_target_format)
        # Add disk xml.
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)

        disk_xml = Disk(type_name=device_type)
        disk_xml.device = device
        if device_type == "file":
            dev_attrs = "file"
        elif device_type == "dir":
            dev_attrs = "dir"
        else:
            dev_attrs = "dev"
        disk_source = disk_xml.new_disk_source(
                **{"attrs": {dev_attrs: image_target_path}})
        if slice_test == "yes":
            slice_size_param = process.run("du -b %s" % image_target_path).stdout_text
            slice_size = re.findall(r'[0-9]+', slice_size_param)
            slice_size = ''.join(slice_size)
            disk_source.slices = disk_xml.new_slices(
                    **{"slice_type": "storage", "slice_offset": "0", "slice_size": slice_size})
        disk_xml.driver = {"name": "qemu", "type": image_target_format,
                           "cache": "none"}
        disk_xml.target = {"dev": device_target, "bus": device_bus}
        if not status_error:
            encryption_dict = {"encryption": 'luks',
                               "secret": {"type": secret_type,
                                          "uuid": sec_encryption_uuid}}
        if encryption_in_source:
            disk_source.encryption = disk_xml.new_encryption(**encryption_dict)
        if encryption_out_source:
            disk_xml.encryption = disk_xml.new_encryption(**encryption_dict)
        disk_xml.source = disk_source
        logging.debug("disk xml is:\n%s" % disk_xml)
        if not hotplug:
            # Sync VM xml.
            vmxml.add_device(disk_xml)
            vmxml.sync()

        try:
            # Start the VM and do disk hotplug if required,
            # then check disk status in vm.
            # Note that LUKS encrypted virtual disk without <encryption>
            # can be normally started or attached since qemu will just treat
            # it as RAW, so we don't test LUKS with status_error=TRUE.
            vm.start()
            vm.wait_for_login()
            if status_error:
                if hotplug:
                    logging.debug("attaching disk, expecting error...")
                    result = virsh.attach_device(vm_name, disk_xml.xml)
                    libvirt.check_exit_status(result, status_error)
                else:
                    test.fail("VM started unexpectedly.")
            else:
                if hotplug:
                    result = virsh.attach_device(vm_name, disk_xml.xml,
                                                 debug=True)
                    libvirt.check_exit_status(result)
                    if not check_in_vm(vm, device_target, old_parts):
                        test.fail("Check encryption disk in VM failed")
                    result = virsh.detach_device(vm_name, disk_xml.xml,
                                                 debug=True, wait_remove_event=True)
                    libvirt.check_exit_status(result)
                else:
                    if not check_in_vm(vm, device_target, old_parts):
                        test.fail("Check encryption disk in VM failed")
        except virt_vm.VMStartError as e:
            if status_error:
                if hotplug:
                    test.fail("In hotplug scenario, VM should "
                              "start successfully but not."
                              "Error: %s", str(e))
                else:
                    logging.debug("VM failed to start as expected."
                                  "Error: %s", str(e))
            else:
                # Libvirt2.5.0 onward,AES-CBC encrypted qcow2 images is no
                # longer supported.
                err_msgs = ("AES-CBC encrypted qcow2 images is"
                            " no longer supported in system emulators")
                if str(e).count(err_msgs):
                    test.cancel(err_msgs)
                else:
                    test.fail("VM failed to start."
                              "Error: %s" % str(e))
    finally:
        # Recover VM.
        if vm.is_alive():
            vm.destroy(gracefully=False)
        logging.info("Restoring vm...")
        vmxml_backup.sync()

        # Clean up image
        for sec_uuid in set(sec_uuids):
            virsh.secret_undefine(sec_uuid, **virsh_dargs)
        if os.path.exists(image_target_path):
            os.remove(image_target_path)
