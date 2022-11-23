import logging as log
import time
import shutil

from avocado.utils import service

from virttest import virt_vm
from virttest import virsh
from virttest import utils_disk

from virttest.utils_test import libvirt

from virttest.libvirt_xml import vm_xml, xcepts
from virttest.libvirt_xml.devices.controller import Controller

from virttest.libvirt_xml.devices.disk import Disk

from virttest import libvirt_version


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test SCSI3 Persistent Reservation functions.

    1.Prepare iscsi backend storage.
    2.Prepare disk xml.
    3.Hot/cold plug the disk to vm.
    4.Check if SCSI3 Persistent Reservation commands can be issued to that disk.
    5.Recover test environment.
    6.Confirm the test result.
    """
    def get_delta_parts(vm, old_parts):
        """
        Get the newly added partitions/blockdevs in vm.
        :param vm: The vm to be operated.
        :param old_parts: The original partitions/blockdevs in vm.
        :return: Newly added partitions/blockdevs.
        """
        session = vm.wait_for_login()
        new_parts = utils_disk.get_parts_list(session)
        new_parts = list(set(new_parts).difference(set(old_parts)))
        session.close()
        return new_parts

    def check_pr_cmds(vm, blk_dev):
        """
        Check if SCSI3 Persistent Reservation commands can be used in vm.
        :param vm: The vm to be checked.
        :param blk_dev: The block device in vm to be checked.
        """
        session = vm.wait_for_login()
        cmd = ("sg_persist --no-inquiry -v --out --register-ignore --param-sark 123aaa /dev/{0} &&"
               "sg_persist --no-inquiry --in -k /dev/{0} &&"
               "sg_persist --no-inquiry -v --out --reserve --param-rk 123aaa --prout-type 5 /dev/{0} &&"
               "sg_persist --no-inquiry --in -r /dev/{0} &&"
               "sg_persist --no-inquiry -v --out --release --param-rk 123aaa --prout-type 5 /dev/{0} &&"
               "sg_persist --no-inquiry --in -r /dev/{0} &&"
               "sg_persist --no-inquiry -v --out --register --param-rk 123aaa --prout-type 5 /dev/{0} &&"
               "sg_persist --no-inquiry --in -k /dev/{0}"
               .format(blk_dev))
        cmd_status, cmd_output = session.cmd_status_output(cmd)
        session.close()
        if cmd_status == 127:
            test.error("sg3_utils not installed in test image")
        elif cmd_status != 0:
            test.fail("persistent reservation failed for /dev/%s" % blk_dev)
        else:
            logging.info("persistent reservation successful for /dev/%s" % blk_dev)

    def start_or_stop_qemu_pr_helper(is_start=True, path_to_sock="/var/run/qemu-pr-helper.sock"):
        """
        Start or stop qemu-pr-helper daemon
        :param is_start: Set True to start, False to stop.
        """
        service_mgr = service.ServiceManager()
        if is_start:
            service_mgr.start('qemu-pr-helper')
            time.sleep(2)
            shutil.chown(path_to_sock, "qemu", "qemu")
        else:
            service_mgr.stop('qemu-pr-helper')

    def ppc_controller_update():
        """
        Update controller of ppc vm to 'virtio-scsi' to support 'scsi' type

        :return:
        """
        if params.get('machine_type') == 'pseries' and device_bus == 'scsi':
            if not vmxml.get_controllers(device_bus, 'virtio-scsi'):
                vmxml.del_controller(device_bus)
                ppc_controller = Controller('controller')
                ppc_controller.type = device_bus
                ppc_controller.index = '0'
                ppc_controller.model = 'virtio-scsi'
                vmxml.add_device(ppc_controller)
                vmxml.sync()

    # Check if SCSI3 Persistent Reservations supported by
    # current libvirt versions.
    if not libvirt_version.version_compare(4, 4, 0):
        test.cancel("The <reservations> tag supported by libvirt from version "
                    "4.4.0")
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    virsh_dargs = {'debug': True, 'ignore_status': True}

    # Disk specific attributes
    device = params.get("virt_disk_device", "lun")
    device_target = params.get("virt_disk_device_target", "sdb")
    device_format = params.get("virt_disk_device_format", "raw")
    device_type = params.get("virt_disk_device_type", "block")
    device_bus = params.get("virt_disk_device_bus", "scsi")
    # Iscsi options
    iscsi_host = params.get("iscsi_host")
    iscsi_port = params.get("iscsi_port")
    emulated_size = params.get("iscsi_image_size", "1G")
    # SCSI3 PR options
    reservations_managed = "yes" == params.get("reservations_managed", "yes")
    reservations_source_type = params.get("reservations_source_type", "unix")
    reservations_source_path = params.get("reservations_source_path",
                                          "/var/run/qemu-pr-helper.sock")
    reservations_source_mode = params.get("reservations_source_mode", "client")
    secret_uuid = ""
    # Case step options
    hotplug_disk = "yes" == params.get("hotplug_disk", "no")

    # Start vm and get all partitions in vm
    if vm.is_dead():
        vm.start()
    session = vm.wait_for_login()
    old_parts = utils_disk.get_parts_list(session)
    session.close()
    vm.destroy(gracefully=False)

    # Back up xml file
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    try:
        # Setup iscsi target
        blk_dev = libvirt.setup_or_cleanup_iscsi(is_setup=True,
                                                 is_login=True,
                                                 image_size=emulated_size,
                                                 portal_ip=iscsi_host)

        # Add disk xml
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        disk_xml = Disk(type_name=device_type)
        disk_xml.device = device
        disk_xml.target = {"dev": device_target, "bus": device_bus}
        driver_dict = {"name": "qemu", "type": device_format}
        disk_xml.driver = driver_dict
        disk_source = disk_xml.new_disk_source(
            **{"attrs": {"dev": blk_dev}})
        if reservations_managed:
            reservations_dict = {"reservations_managed": "yes"}
        else:
            start_or_stop_qemu_pr_helper(path_to_sock=reservations_source_path)
            reservations_dict = {"reservations_managed": "no",
                                 "reservations_source_type": reservations_source_type,
                                 "reservations_source_path": reservations_source_path,
                                 "reservations_source_mode": reservations_source_mode}
        disk_source.reservations = disk_xml.new_reservations(**reservations_dict)
        disk_xml.source = disk_source

        # Update controller of ppc vms
        ppc_controller_update()

        if not hotplug_disk:
            vmxml.add_device(disk_xml)
        try:
            # Start the VM and check status
            vmxml.sync()
            vm.start()
            vm.wait_for_login().close()
            time.sleep(5)
            if hotplug_disk:
                result = virsh.attach_device(vm_name, disk_xml.xml,
                                             ignore_status=True, debug=True)
                libvirt.check_exit_status(result)
            new_parts = get_delta_parts(vm, old_parts)
            if len(new_parts) != 1:
                logging.error("Expected 1 dev added but has %s" % len(new_parts))
            new_part = new_parts[0]
            check_pr_cmds(vm, new_part)
            result = virsh.detach_device(vm_name, disk_xml.xml,
                                         ignore_status=True, debug=True, wait_for_event=True)
            libvirt.check_exit_status(result)
        except virt_vm.VMStartError as e:
            test.fail("VM failed to start."
                      "Error: %s" % str(e))
        except xcepts.LibvirtXMLError as xml_error:
            test.fail("Failed to define VM:\n%s" % xml_error)

    finally:
        # Recover VM.
        if vm.is_alive():
            vm.destroy(gracefully=False)
        vmxml_backup.sync("--snapshots-metadata")
        # Delete the tmp files.
        libvirt.setup_or_cleanup_iscsi(is_setup=False)
        # Stop qemu-pr-helper daemon
        start_or_stop_qemu_pr_helper(is_start=False)
