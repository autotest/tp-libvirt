import os
import logging
from autotest.client import utils
from autotest.client.shared import error
from virttest import aexpect, virt_vm, virsh, remote, qemu_storage
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.disk import Disk
from virttest.libvirt_xml.devices.controller import Controller


def run(test, params, env):
    """
    Test disk attachement of multiple disks.

    1.Prepare test environment, destroy VMs.
    2.Perform 'qemu-img create' operation.
    3.Edit disks xml and start the domains.
    4.Perform test operation.
    5.Recover test environment.
    6.Confirm the test result.
    """

    def set_vm_controller_xml(vmxml):
        """
        Set VM scsi controller xml.

        :param vmxml. Domain xml object.
        """
        # Add disk scsi controller
        scsi_controller = Controller("controller")
        scsi_controller.type = "scsi"
        scsi_controller.index = "0"
        scsi_controller.model = "virtio-scsi"
        vmxml.add_device(scsi_controller)

        # Redefine domain
        vmxml.sync()

    def get_vm_disk_xml(dev_type, dev_name, **options):
        """
        Create a disk xml object and return it.

        :param dev_type. Disk type.
        :param dev_name. Disk device name.
        :param options. Disk options.
        :return: Disk xml object.
        """
        # Create disk xml
        disk_xml = Disk(type_name=dev_type)
        disk_xml.device = "disk"
        if options.has_key("sgio") and options["sgio"] != "":
            disk_xml.sgio = options["sgio"]
            disk_xml.device = "lun"
            disk_xml.rawio = "no"

        if dev_type == "block":
            disk_attr = "dev"
        else:
            disk_attr = "file"

        disk_xml.target = {'dev': options["target"],
                           'bus': options["bus"]}
        disk_xml.source = disk_xml.new_disk_source(
            **{'attrs': {disk_attr: dev_name}})

        # Add driver options from parameters.
        driver_dict = {"name": "qemu"}
        if options.has_key("driver"):
            for driver_option in options["driver"].split(','):
                if driver_option != "":
                    d = driver_option.split('=')
                    logging.debug("disk driver option: %s=%s", d[0], d[1])
                    driver_dict.update({d[0].strip(): d[1].strip()})

        disk_xml.driver = driver_dict
        if options.has_key("share"):
            if options["share"] == "shareable":
                disk_xml.share = True

        return disk_xml

    vm_names = params.get("vms").split()
    if len(vm_names) < 2:
        raise error.TestNAError("No multi vms provided.")

    # Disk specific attributes.
    vms_sgio = params.get("virt_disk_vms_sgio", "").split()
    vms_share = params.get("virt_disk_vms_share", "").split()
    disk_bus = params.get("virt_disk_bus", "virtio")
    disk_target = params.get("virt_disk_target", "vdb")
    disk_type = params.get("virt_disk_type", "file")
    disk_format = params.get("virt_disk_format", "")
    scsi_options = params.get("scsi_options", "")
    disk_driver_options = params.get("disk_driver_options", "")
    hotplug = "yes" == params.get("virt_disk_vms_hotplug", "no")
    status_error = params.get("status_error").split()
    test_error_policy = "yes" == params.get("virt_disk_test_error_policy",
                                            "no")
    test_shareable = "yes" == params.get("virt_disk_test_shareable", "no")
    disk_source_path = test.virtdir

    disks = []
    if disk_format == "scsi":
        disk_source = libvirt.create_scsi_disk(scsi_options)
        if not disk_source:
            raise error.TestNAError("Get scsi disk failed.")
        disks.append({"format": "scsi", "source": disk_source})

    elif disk_format == "iscsi":
        # Create iscsi device if neened.
        image_size = params.get("image_size", "2G")
        disk_source = libvirt.setup_or_cleanup_iscsi(
            is_setup=True, is_login=True, image_size=image_size)
        logging.debug("iscsi dev name: %s", disk_source)
        # Format the disk and make the file system.
        libvirt.mk_part(disk_source)
        libvirt.mkfs("%s1" % disk_source, "ext3")
        disk_source += "1"
        disks.append({"format": disk_format,
                      "source": disk_source})
    elif disk_format in ["raw", "qcow2"]:
        disk_path = "%s/test.%s" % (disk_source_path, disk_format)
        disk_source = libvirt.create_local_disk("file", disk_path, "1",
                                                disk_format=disk_format)
        libvirt.mkfs(disk_source, "ext3")
        disks.append({"format": disk_format,
                      "source": disk_source})

    # Backup xml files, compose the new domain xml
    vms_list = []
    for i in range(len(vm_names)):
        vm_xml_file = os.path.join(test.tmpdir,
                                   vm_names[i] + "_backup_.xml")
        virsh.dumpxml(vm_names[i], extra="--inactive",
                      to_file=vm_xml_file)
        vm = env.get_vm(vm_names[i])
        # Destroy domain first.
        if vm.is_alive():
            vm.destroy(gracefully=False)

        # Configure vm disk options and define vm
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_names[i])
        if disk_bus == "scsi":
            set_vm_controller_xml(vmxml)
        disk_sgio = ""
        if len(vms_sgio) > i:
            disk_sgio = vms_sgio[i]
        shareable = ""
        if len(vms_share) > i:
            shareable = vms_share[i]
        disk_xml = get_vm_disk_xml(disk_type, disk_source,
                                   sgio=disk_sgio, share=shareable,
                                   target=disk_target, bus=disk_bus,
                                   driver=disk_driver_options)
        if not hotplug:
            # If we are not testing hotplug,
            # add disks to domain xml and sync.
            vmxml.add_device(disk_xml)
            vmxml.sync()
        vms_list.append({"name": vm_names[i], "vm": vm,
                         "backup": vm_xml_file,
                         "status": "yes" == status_error[i],
                         "disk": disk_xml})
        logging.debug("vms_list %s" % vms_list)

    try:
        for i in range(len(vms_list)):
            try:
                # Try to start the domain.
                vms_list[i]['vm'].start()
                # Check if VM is started as expected.
                if not vms_list[i]['status']:
                    raise error.TestFail('VM started unexpectedly.')

                session = vms_list[i]['vm'].wait_for_login()
                # if we are testing hotplug, it need to start domain and
                # then run virsh attach-device command.
                if hotplug:
                    vms_list[i]['disk'].xmltreefile.write()
                    result = virsh.attach_device(vms_list[i]['name'],
                                                 vms_list[i]['disk'].xml).exit_status
                    os.remove(vms_list[i]['disk'].xml)

                    # Check if the return code of attach-device
                    # command is as expected.
                    if 0 != result and vms_list[i]['status']:
                        raise error.TestFail('Failed to hotplug disk device')
                    elif 0 == result and not vms_list[i]['status']:
                        raise error.TestFail('Hotplug disk device unexpectedly.')

                # Check disk error_policy option in VMs.
                if test_error_policy:
                    error_policy = vms_list[i]['disk'].driver["error_policy"]
                    if i == 0:
                        # If we testing enospace error policy, only 1 vm used
                        if error_policy == "enospace":
                            cmd = ("mount /dev/%s /mnt && dd if=/dev/zero of=/mnt/test"
                                   " bs=1M count=2000 2>&1 | grep 'No space left'"
                                   % disk_target)
                            s, o = session.cmd_status_output(cmd)
                            logging.debug("error_policy in vm0 exit %s; output: %s", s, o)
                            if 0 != s:
                                raise error.TestFail("Test error_policy %s: cann't see"
                                                     " error messages")
                            session.close()
                            break

                        if session.cmd_status("fdisk -l /dev/%s && mount /dev/%s /mnt; ls /mnt"
                                              % (disk_target, disk_target)):
                            session.close()
                            raise error.TestFail("Test error_policy: "
                                                 "failed to mount disk")
                    if i == 1:
                        try:
                            session0 = vms_list[0]['vm'].wait_for_login(timeout=10)
                            cmd = ("fdisk -l /dev/%s && mkfs.ext3 -F /dev/%s "
                                   % (disk_target, disk_target))
                            s, o = session.cmd_status_output(cmd)
                            logging.debug("error_policy in vm1 exit %s; output: %s", s, o)
                            session.close()
                            cmd = ("dd if=/dev/zero of=/mnt/test bs=1M count=100 && dd if="
                                   "/mnt/test of=/dev/null bs=1M;dmesg | grep 'I/O error'")
                            s, o = session0.cmd_status_output(cmd)
                            logging.debug("session in vm0 exit %s; output: %s", s, o)
                            if error_policy == "report":
                                if s:
                                    raise error.TestFail("Test error_policy %s: cann't report"
                                                         " error" % error_policy)
                            elif error_policy == "ignore":
                                if 0 == s:
                                    raise error.TestFail("Test error_policy %s: error cann't"
                                                         " be ignored" % error_policy)
                            session0.close()
                        except (remote.LoginError, virt_vm.VMError, aexpect.ShellError), e:
                            if error_policy == "stop":
                                if not vms_list[0]['vm'].is_paused():
                                    raise error.TestFail("Test error_policy %s: cann't stop"
                                                         " VM" % error_policy)
                            else:
                                logging.error(str(e))
                                raise error.TestFail("Test error_policy %s: login failed"
                                                     % error_policy)

                if test_shareable:
                    if i == 1:
                        try:
                            test_str = "teststring"
                            # Try to write on vm0.
                            session0 = vms_list[0]['vm'].wait_for_login(timeout=10)
                            cmd = ("fdisk -l /dev/%s && mount /dev/%s /mnt && echo '%s' "
                                   "> /mnt/test && umount /mnt"
                                   % (disk_target, disk_target, test_str))
                            s, o = session0.cmd_status_output(cmd)
                            logging.debug("session in vm0 exit %s; output: %s", s, o)
                            if s:
                                raise error.TestFail("Test disk shareable on VM0 failed")
                            session0.close()
                            # Try to read on vm1.
                            cmd = ("fdisk -l /dev/%s && mount /dev/%s /mnt && grep %s"
                                   " /mnt/test && umount /mnt"
                                   % (disk_target, disk_target, test_str))
                            s, o = session.cmd_status_output(cmd)
                            logging.debug("session in vm1 exit %s; output: %s", s, o)
                            if s:
                                raise error.TestFail("Test disk shareable on VM1 failed")
                        except (remote.LoginError, virt_vm.VMError, aexpect.ShellError), e:
                            logging.error(str(e))
                            raise error.TestFail("Test disk shareable: login failed")
                session.close()
            except virt_vm.VMStartError:
                if vms_list[i]['status']:
                    raise error.TestFail('VM Failed to start'
                                         ' for some reason!')
    finally:
        # Recover VMs.
        for i in range(len(vms_list)):
            if vms_list[i]['vm'].is_alive():
                vms_list[i]['vm'].destroy(gracefully=False)
            logging.info("Restoring vm...")
            virsh.undefine(vms_list[i]['name'])
            virsh.define(vms_list[i]['backup'])
            os.remove(vms_list[i]['backup'])

        # Remove disks.
        for img in disks:
            if img["format"] == "scsi":
                libvirt.delete_scsi_disk()
            elif img["format"] == "iscsi":
                libvirt.setup_or_cleanup_iscsi(is_setup=False)
            elif img.has_key("source"):
                os.remove(img["source"])
