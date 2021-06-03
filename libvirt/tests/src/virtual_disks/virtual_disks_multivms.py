import os
import logging
import aexpect

from avocado.utils import process

from virttest import utils_selinux
from virttest import data_dir
from virttest import virt_vm
from virttest import virsh
from virttest import remote
from virttest import utils_misc
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.disk import Disk
from virttest.libvirt_xml.devices.controller import Controller

from virttest import libvirt_version


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
        disk_xml.device = options["disk_device"]
        if "sgio" in options and options["sgio"] != "":
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
        if "driver" in options:
            for driver_option in options["driver"].split(','):
                if driver_option != "":
                    d = driver_option.split('=')
                    logging.debug("disk driver option: %s=%s", d[0], d[1])
                    driver_dict.update({d[0].strip(): d[1].strip()})

        disk_xml.driver = driver_dict
        if "share" in options:
            if options["share"] == "shareable":
                disk_xml.share = True

        if "readonly" in options:
            if options["readonly"] == "readonly":
                disk_xml.readonly = True

        logging.debug("The disk xml is: %s" % disk_xml.xmltreefile)

        return disk_xml

    vm_names = params.get("vms").split()
    if len(vm_names) < 2:
        test.cancel("No multi vms provided.")

    # Disk specific attributes.
    vms_sgio = params.get("virt_disk_vms_sgio", "").split()
    vms_share = params.get("virt_disk_vms_share", "").split()
    vms_readonly = params.get("virt_disk_vms_readonly", "").split()
    disk_bus = params.get("virt_disk_bus", "virtio")
    disk_target = params.get("virt_disk_target", "vdb")
    disk_type = params.get("virt_disk_type", "file")
    disk_device = params.get("virt_disk_device", "disk")
    disk_format = params.get("virt_disk_format", "")
    scsi_options = params.get("scsi_options", "")
    disk_driver_options = params.get("disk_driver_options", "")
    hotplug = "yes" == params.get("virt_disk_vms_hotplug", "no")
    status_error = params.get("status_error").split()
    test_error_policy = "yes" == params.get("virt_disk_test_error_policy",
                                            "no")
    test_shareable = "yes" == params.get("virt_disk_test_shareable", "no")
    test_readonly = "yes" == params.get("virt_disk_test_readonly", "no")
    disk_source_path = data_dir.get_data_dir()
    disk_path = ""
    tmp_filename = "cdrom_te.tmp"
    tmp_readonly_file = ""

    # Backup vm xml files.
    vms_backup = []
    # We just use 2 VMs for testing.
    for i in list(range(2)):
        vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_names[i])
        vms_backup.append(vmxml_backup)
    # Initialize VM list
    vms_list = []
    try:
        # Create disk images if needed.
        disks = []
        if disk_format == "scsi":
            disk_source = libvirt.create_scsi_disk(scsi_options)
            if not disk_source:
                test.cancel("Get scsi disk failed.")
            disks.append({"format": "scsi", "source": disk_source})

        elif disk_format == "iscsi":
            # Create iscsi device if neened.
            image_size = params.get("image_size", "100M")
            disk_source = libvirt.setup_or_cleanup_iscsi(
                is_setup=True, is_login=True, image_size=image_size)
            logging.debug("iscsi dev name: %s", disk_source)
            # Format the disk and make the file system.
            libvirt.mk_label(disk_source)
            libvirt.mk_part(disk_source, size="10M")
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

        if disk_device == "cdrom":
            tmp_readonly_file = "/root/%s" % tmp_filename
            with open(tmp_readonly_file, 'w') as f:
                f.write("teststring\n")
            disk_path = "%s/test.iso" % disk_source_path
            disk_source = libvirt.create_local_disk("iso", disk_path, "1")
            disks.append({"source": disk_source})

        # Compose the new domain xml
        for i in list(range(2)):
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

            # Since lock feature is introduced in libvirt 3.9.0 afterwards, disk shareable attribute
            # need be set if both of VMs need be started successfully in case they share the same disk
            if test_error_policy and libvirt_version.version_compare(3, 9, 0):
                vms_share = ["shareable", "shareable"]
            if len(vms_share) > i:
                shareable = vms_share[i]
            readonly = ""
            if len(vms_readonly) > i:
                readonly = vms_readonly[i]
            disk_xml = get_vm_disk_xml(disk_type, disk_source,
                                       sgio=disk_sgio, share=shareable,
                                       target=disk_target, bus=disk_bus,
                                       driver=disk_driver_options,
                                       disk_device=disk_device,
                                       readonly=readonly)
            if not hotplug:
                # If we are not testing hotplug,
                # add disks to domain xml and sync.
                vmxml.add_device(disk_xml)
                vmxml.sync()
            vms_list.append({"name": vm_names[i], "vm": vm,
                             "status": "yes" == status_error[i],
                             "disk": disk_xml})
            logging.debug("vms_list %s" % vms_list)

        for i in list(range(len(vms_list))):
            try:
                # Try to start the domain.
                vms_list[i]['vm'].start()
                # Check if VM is started as expected.
                if not vms_list[i]['status']:
                    test.fail('VM started unexpectedly.')

                session = vms_list[i]['vm'].wait_for_login()
                # if we are testing hotplug, it need to start domain and
                # then run virsh attach-device command.
                if hotplug:
                    vms_list[i]['disk'].xmltreefile.write()
                    result = virsh.attach_device(vms_list[i]['name'],
                                                 vms_list[i]['disk'].xml,
                                                 debug=True).exit_status
                    os.remove(vms_list[i]['disk'].xml)

                    # Check if the return code of attach-device
                    # command is as expected.
                    if 0 != result and vms_list[i]['status']:
                        test.fail('Failed to hotplug disk device')
                    elif 0 == result and not vms_list[i]['status']:
                        test.fail('Hotplug disk device unexpectedly.')

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
                                test.fail("Test error_policy %s: cann't see"
                                          " error messages")
                            session.close()
                            break

                        if session.cmd_status("fdisk -l /dev/%s && mount /dev/%s /mnt; ls /mnt"
                                              % (disk_target, disk_target)):
                            session.close()
                            test.fail("Test error_policy: "
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
                                process.run("rm -rf %s" % disk_source, ignore_status=False, shell=True)
                                vms_list[0]['vm'].destroy(gracefully=False)

                                def _check_error():
                                    cmd_result = virsh.domblkerror(vms_list[0]['name'])
                                    return 'Segmentation fault' in cmd_result.stdout_text.strip()
                                status = utils_misc.wait_for(lambda: _check_error, timeout=90)
                                if not status:
                                    test.fail("Test error_policy %s: cann't report"
                                              " error" % error_policy)
                            elif error_policy == "ignore":
                                if 0 == s:
                                    test.fail("Test error_policy %s: error cann't"
                                              " be ignored" % error_policy)
                            session0.close()
                        except (remote.LoginError, virt_vm.VMError, aexpect.ShellError) as e:
                            if error_policy == "stop":
                                if not vms_list[0]['vm'].is_paused():
                                    test.fail("Test error_policy %s: cann't stop"
                                              " VM" % error_policy)
                            else:
                                logging.error(str(e))
                                test.fail("Test error_policy %s: login failed"
                                          % error_policy)

                if test_shareable:
                    # Check shared file selinux label with type and MCS as
                    # svirt_image_t:s0
                    if disk_path:
                        if not utils_selinux.check_context_of_file(disk_path, "svirt_image_t:s0"):
                            test.fail("Context of shared iso is not expected.")

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
                                test.fail("Test disk shareable on VM0 failed")
                            session0.close()
                            # Try to read on vm1.
                            cmd = ("fdisk -l /dev/%s && mount /dev/%s /mnt && grep %s"
                                   " /mnt/test && umount /mnt"
                                   % (disk_target, disk_target, test_str))
                            s, o = session.cmd_status_output(cmd)
                            logging.debug("session in vm1 exit %s; output: %s", s, o)
                            if s:
                                test.fail("Test disk shareable on VM1 failed")
                        except (remote.LoginError, virt_vm.VMError, aexpect.ShellError) as e:
                            logging.error(str(e))
                            test.fail("Test disk shareable: login failed")

                if test_readonly:
                    # Check shared file selinux label with type and MCS as
                    # virt_content_t:s0
                    if disk_path:
                        if not utils_selinux.check_context_of_file(disk_path, "virt_content_t:s0"):
                            test.fail("Context of shared iso is not expected.")
                    if i == 1:
                        try:
                            test_str = "teststring"
                            # Try to read on vm0.
                            session0 = vms_list[0]['vm'].wait_for_login(timeout=10)
                            cmd = "mount -o ro /dev/cdrom /mnt && grep "
                            cmd += "%s /mnt/%s" % (test_str, tmp_filename)
                            s, o = session0.cmd_status_output(cmd)
                            logging.debug("session in vm0 exit %s; output: %s", s, o)
                            session0.close()
                            if s:
                                test.fail("Test file not found in VM0 cdrom")
                            # Try to read on vm1.
                            s, o = session.cmd_status_output(cmd)
                            logging.debug("session in vm1 exit %s; output: %s", s, o)
                            if s:
                                test.fail("Test file not found in VM1 cdrom")
                        except (remote.LoginError, virt_vm.VMError, aexpect.ShellError) as e:
                            logging.error(str(e))
                            test.fail("Test disk shareable: login failed")
                session.close()
            except virt_vm.VMStartError as start_error:
                if vms_list[i]['status']:
                    test.fail("VM failed to start."
                              "Error: %s" % str(start_error))
    finally:
        # Stop VMs.
        for i in list(range(len(vms_list))):
            if vms_list[i]['vm'].is_alive():
                vms_list[i]['vm'].destroy(gracefully=False)

        # Recover VMs.
        for vmxml_backup in vms_backup:
            vmxml_backup.sync()

        # Remove disks.
        for img in disks:
            if 'format' in img:
                if img["format"] == "scsi":
                    utils_misc.wait_for(libvirt.delete_scsi_disk,
                                        120, ignore_errors=True)
                elif img["format"] == "iscsi":
                    libvirt.setup_or_cleanup_iscsi(is_setup=False)
            elif "source" in img:
                os.remove(img["source"])

        if tmp_readonly_file:
            if os.path.exists(tmp_readonly_file):
                os.remove(tmp_readonly_file)
