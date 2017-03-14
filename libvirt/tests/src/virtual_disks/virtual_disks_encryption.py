import logging
import os
import re
import base64

import aexpect

from avocado.core import exceptions
from avocado.utils import process

from virttest import remote
from virttest import virt_vm
from virttest import virsh
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml import vol_xml
from virttest.libvirt_xml import pool_xml
from virttest.libvirt_xml import secret_xml
from virttest.libvirt_xml.devices.disk import Disk


def run(test, params, env):
    """
    Test disk encryption option.

    1.Prepare test environment,destroy or suspend a VM.
    2.Prepare pool, volume.
    3.Edit disks xml and start the domain.
    4.Perform test operation.
    5.Recover test environment.
    6.Confirm the test result.
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    virsh_dargs = {'debug': True, 'ignore_status': True}

    def create_pool(p_name, p_type, p_target):
        """
        Define and start a pool.

        :param p_name. Pool name.
        :param p_type. Pool type.
        :param p_target. Pool target path.
        """
        p_xml = pool_xml.PoolXML(pool_type=p_type)
        p_xml.name = p_name
        p_xml.target_path = p_target

        if not os.path.exists(p_target):
            os.mkdir(p_target)
        p_xml.xmltreefile.write()
        ret = virsh.pool_define(p_xml.xml, **virsh_dargs)
        libvirt.check_exit_status(ret)
        ret = virsh.pool_build(p_name, **virsh_dargs)
        libvirt.check_exit_status(ret)
        ret = virsh.pool_start(p_name, **virsh_dargs)
        libvirt.check_exit_status(ret)

    def create_vol(p_name, target_encrypt_params, vol_params):
        """
        Create volume.

        :param p_name. Pool name.
        :param target_encrypt_params encrypt parameters in dict.
        :param vol_params. Volume parameters dict.
        :return: True if create successfully.
        """
        volxml = vol_xml.VolXML()
        v_xml = volxml.new_vol(**vol_params)
        v_xml.encryption = volxml.new_encryption(**target_encrypt_params)
        v_xml.xmltreefile.write()

        ret = virsh.vol_create(p_name, v_xml.xml, **virsh_dargs)
        libvirt.check_exit_status(ret)

    def create_secret(vol_path):
        """
        Create secret.

        :param vol_path. volume path.
        :return: secret id if create successfully.
        """
        sec_xml = secret_xml.SecretXML("no", "yes")
        sec_xml.description = "volume secret"

        sec_xml.usage = 'volume'
        sec_xml.volume = vol_path
        sec_xml.xmltreefile.write()

        ret = virsh.secret_define(sec_xml.xml)
        libvirt.check_exit_status(ret)
        # Get secret uuid.
        try:
            encryption_uuid = re.findall(r".+\S+(\ +\S+)\ +.+\S+",
                                         ret.stdout)[0].lstrip()
        except IndexError, e:
            raise exceptions.TestError("Fail to get newly created secret uuid")
        logging.debug("Secret uuid %s", encryption_uuid)

        # Set secret value.
        secret_string = base64.b64encode(secret_password_no_encoded)
        ret = virsh.secret_set_value(encryption_uuid, secret_string,
                                     **virsh_dargs)
        libvirt.check_exit_status(ret)
        return encryption_uuid

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
                raise exceptions.TestFail("Failed to query/install parted, make sure"
                                          " that you have usable repo in guest")

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

            libvirt.mk_part("/dev/%s" % added_part, size="10M", session=session)
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
        except (remote.LoginError, virt_vm.VMError, aexpect.ShellError), e:
            logging.error(str(e))
            return False

    # Disk specific attributes.
    device = params.get("virt_disk_device", "disk")
    device_target = params.get("virt_disk_device_target", "vdd")
    device_type = params.get("virt_disk_device_type", "file")
    device_bus = params.get("virt_disk_device_bus", "virtio")

    # Pool/Volume options.
    pool_name = params.get("pool_name")
    pool_type = params.get("pool_type")
    pool_target = params.get("pool_target")
    volume_name = params.get("vol_name")
    volume_alloc = params.get("vol_alloc")
    volume_cap_unit = params.get("vol_cap_unit")
    volume_cap = params.get("vol_cap")
    volume_target_path = params.get("target_path")
    volume_target_format = params.get("target_format")
    volume_target_encypt = params.get("target_encypt", "")
    volume_target_label = params.get("target_label")

    status_error = "yes" == params.get("status_error")
    secret_type = params.get("secret_type", "passphrase")
    secret_password_no_encoded = params.get("secret_password_no_encoded", "redhat")
    virt_disk_qcow2_format = "yes" == params.get("virt_disk_qcow2_format")

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    # Start vm and get all partions in vm.
    if vm.is_dead():
        vm.start()
    session = vm.wait_for_login()
    old_parts = libvirt.get_parts_list(session)
    session.close()
    vm.destroy(gracefully=False)

    # Back up xml file.
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    sec_encryption_uuid = None
    try:
        # Prepare the disk.
        sec_uuids = []
        create_pool(pool_name, pool_type, pool_target)
        vol_params = {"name": volume_name, "capacity": int(volume_cap),
                      "allocation": int(volume_alloc), "format":
                      volume_target_format, "path": volume_target_path,
                      "label": volume_target_label,
                      "capacity_unit": volume_cap_unit}
        vol_encryption_params = {}
        vol_encryption_params.update({"format": volume_target_encypt})
        # For any disk format other than qcow2, it need create secret firstly.
        if not virt_disk_qcow2_format:
            # create secret.
            sec_encryption_uuid = create_secret(volume_target_path)
            sec_uuids.append(sec_encryption_uuid)
            vol_encryption_params.update({"secret": {"type": secret_type, "uuid": sec_encryption_uuid}})
        try:
            # If Libvirt version is lower than 2.5.0
            # Creating luks encryption volume is not supported,so skip it.
            create_vol(pool_name, vol_encryption_params, vol_params)
        except AssertionError, info:
            err_msgs = ("create: invalid option")
            if str(info).count(err_msgs):
                raise exceptions.TestSkipError("Creating luks encryption volume "
                                               "is not supported on this libvirt version")
            else:
                raise exceptions.TestError("Failed to create volume."
                                           "Error: %s" % str(info))
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
        disk_xml.source = disk_xml.new_disk_source(
            **{"attrs": {dev_attrs: volume_target_path}})
        disk_xml.driver = {"name": "qemu", "type": volume_target_format,
                           "cache": "none"}
        disk_xml.target = {"dev": device_target, "bus": device_bus}

        v_xml = vol_xml.VolXML.new_from_vol_dumpxml(volume_name, pool_name)

        sec_uuids.append(v_xml.encryption.secret["uuid"])
        if not status_error:
            logging.debug("vol info -- format: %s, type: %s, uuid: %s",
                          v_xml.encryption.format,
                          v_xml.encryption.secret["type"],
                          v_xml.encryption.secret["uuid"])
            disk_xml.encryption = disk_xml.new_encryption(
                **{"encryption": v_xml.encryption.format, "secret": {
                    "type": v_xml.encryption.secret["type"],
                    "uuid": v_xml.encryption.secret["uuid"]}})
        # Sync VM xml.
        vmxml.add_device(disk_xml)
        vmxml.sync()

        try:
            # Start the VM and check status.
            vm.start()
            if status_error:
                raise exceptions.TestFail("VM started unexpectedly.")

            if not check_in_vm(vm, device_target, old_parts):
                raise exceptions.TestFail("Check encryption disk in VM failed")
        except virt_vm.VMStartError, e:
            if status_error:
                logging.debug("VM failed to start as expected."
                              "Error: %s", str(e))
                pass
            else:
                # Libvirt2.5.0 onward,AES-CBC encrypted qcow2 images is longer supported.
                err_msgs = ("AES-CBC encrypted qcow2 images is"
                            " no longer supported in system emulators")
                if str(e).count(err_msgs):
                    exceptions.TestSkipError(err_msgs)
                else:
                    raise exceptions.TestFail("VM failed to start."
                                              "Error: %s" % str(e))
    finally:
        # Recover VM.
        if vm.is_alive():
            vm.destroy(gracefully=False)
        logging.info("Restoring vm...")
        vmxml_backup.sync()

        # Clean up pool, vol
        for sec_uuid in set(sec_uuids):
            virsh.secret_undefine(sec_uuid, **virsh_dargs)
            virsh.vol_delete(volume_name, pool_name, **virsh_dargs)
        if virsh.pool_state_dict().has_key(pool_name):
            virsh.pool_destroy(pool_name, **virsh_dargs)
            virsh.pool_undefine(pool_name, **virsh_dargs)
