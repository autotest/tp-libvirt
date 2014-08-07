import logging
import os
from autotest.client.shared import error
from virttest.utils_test import libvirt
from virttest import aexpect, remote, virt_vm, virsh, libvirt_storage
from virttest.libvirt_xml import vm_xml, vol_xml, pool_xml
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

    def create_vol(p_name, p_format, vol_params):
        """
        Create volume.

        :param p_name. Pool name.
        :param vol_params. Volume parameters dict.
        :return: True if create successfully.
        """
        volxml = vol_xml.VolXML()
        v_xml = volxml.new_vol(**vol_params)
        v_xml.encryption = volxml.new_encryption(
            **{"format": p_format})
        v_xml.xmltreefile.write()
        ret = virsh.vol_create(p_name, v_xml.xml, **virsh_dargs)
        libvirt.check_exit_status(ret)

    def check_in_vm(vm, target):
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
                raise error.TestFail("Failed to query/install parted, make sure"
                                     " that you have usable repo in guest")

            if target == "hda":
                target = "sda"
            libvirt.mk_part("/dev/%s" % target, session=session)
            libvirt.mkfs("/dev/%s1" % target, "ext3", session=session)

            cmd = ("mount /dev/%s1 /mnt && echo '123' > /mnt/testfile"
                   " && cat /mnt/testfile && umount /mnt" % target)
            s, o = session.cmd_status_output(cmd)
            logging.info("Check disk operation in VM:\n%s", o)
            if s != 0:
                session.close()
                return False
            return True
        except (remote.LoginError, virt_vm.VMError, aexpect.ShellError), e:
            logging.error(str(e))
            return False

    # Disk specific attributes.
    device = params.get("virt_disk_device", "disk")
    device_source_name = params.get("virt_disk_device_source")
    device_target = params.get("virt_disk_device_target", "vdd")
    device_cache = params.get("virt_disk_device_cache", "")
    device_format = params.get("virt_disk_device_format", "raw")
    device_type = params.get("virt_disk_device_type", "file")
    device_bus = params.get("virt_disk_device_bus", "virtio")
    driver_options = params.get("driver_option", "").split(',')

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

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    # Destroy VM first.
    if vm.is_alive():
        vm.destroy(gracefully=False)

    # Back up xml file.
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    try:
        # Prepare the disk.
        sec_uuid = []
        create_pool(pool_name, pool_type, pool_target)
        vol_params = {"name": volume_name, "capacity": int(volume_cap),
                      "allocation": int(volume_alloc), "format":
                      volume_target_format, "path": volume_target_path,
                      "label": volume_target_label,
                      "capacity_unit": volume_cap_unit}
        create_vol(pool_name, volume_target_encypt, vol_params)

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
        sec_uuid.append(v_xml.encryption.secret["uuid"])
        if not status_error:
            logging.debug("vol info -- format: %s, type: %s, uuid: %s",
                          v_xml.encryption.format, v_xml.encryption.secret["type"],
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
                raise error.TestFail("VM started unexpectedly.")

            if not check_in_vm(vm, device_target):
                raise error.TestFail("Check encryption disk in VM failed")
        except virt_vm.VMStartError, e:
            if status_error:
                logging.debug("VM failed to start as expected."
                              "Error: %s" % str(e))
                pass
            else:
                raise error.TestFail("VM failed to start."
                                     "Error: %s" % str(e))

    finally:
        # Recover VM.
        if vm.is_alive():
            vm.destroy(gracefully=False)
        logging.info("Restoring vm...")
        vmxml_backup.sync()

        # Clean up pool, vol
        for i in sec_uuid:
            virsh.secret_undefine(i, **virsh_dargs)
            virsh.vol_delete(volume_name, pool_name, **virsh_dargs)
        if virsh.pool_state_dict().has_key(pool_name):
            virsh.pool_destroy(pool_name, **virsh_dargs)
            virsh.pool_undefine(pool_name, **virsh_dargs)
