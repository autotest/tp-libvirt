import os
import logging
import aexpect

from avocado.utils import process
from avocado.utils import astring

from virttest import data_dir
from virttest import virt_vm
from virttest import virsh
from virttest import remote
from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_disk
from virttest.libvirt_xml import vm_xml

from virttest import libvirt_version


def run(test, params, env):
    """
    Test <transient/> disks.

    1.Prepare test environment, destroy VMs.
    2.Perform 'qemu-img create' operation.
    3.Edit disks xml and start the domains.
    4.Perform test operation.
    5.Recover test environment.
    6.Confirm the test result.
    """

    def check_transient_disk_keyword(vm_names):
        """
        Check VM disk with TRANSIENT keyword.

        :param vm_names. VM names list.
        """
        logging.info("Checking disk with transient keyword...")

        output0 = ""
        output1 = ""
        for i in list(range(2)):
            ret = virsh.dumpxml(vm_names[i], ignore_status=False)

            cmd = ("echo \"%s\" | grep '<source file=.*TRANSIENT.*/>'" % ret.stdout_text)
            if process.system(cmd, ignore_status=False, shell=True):
                test.fail("Check transident disk on %s failed" % vm_names[i])
            if i == 0:
                output0 = astring.to_text(process.system_output(cmd, ignore_status=False, shell=True))
            else:
                output1 = astring.to_text(process.system_output(cmd, ignore_status=False, shell=True))
        if output0 == output1:
            test.fail("Two vms have same source transident disk %s" % output0)

    def check_share_transient_disk(vms_list):
        """
        Check share base image of <transient/> disks.

        :param vms_list. VM object list.
        """
        logging.info("Checking share base image of transient disk...")

        try:
            test_str = "teststring"
            sha_cmd = ("sha1sum /dev/%s" % disk_target)
            cmd = ("fdisk -l /dev/%s && mkfs.ext4 -F /dev/%s && mount /dev/%s"
                   " /mnt && echo '%s' > /mnt/test && umount /mnt"
                   % (disk_target, disk_target, disk_target, test_str))

            # check on vm0.
            session0 = vms_list[0]['vm'].wait_for_login(timeout=10)
            s, o = session0.cmd_status_output(cmd)
            logging.debug("session in vm0 exit %s; output: %s", s, o)
            if s:
                session0.close()
                test.fail("Shared disk on vm0 doesn't work well")

            vm0_disk_sha1 = session0.cmd_output(sha_cmd)
            session0.close()
            vms_list[0]['vm'].destroy(gracefully=False)

            # check on vm1.
            session = vms_list[1]['vm'].wait_for_login(timeout=10)
            vm1_disk_sha1 = session.cmd_output(sha_cmd)
            if vm0_disk_sha1 == vm1_disk_sha1:
                session.close()
                test.fail("Still can find file created in transient disk of vm0")

            s, o = session.cmd_status_output(cmd)
            logging.debug("session in vm1 exit %s; output: %s", s, o)
            if s:
                session.close()
                test.fail("Shared disk on vm1 doesn't work well")
            session.close()
        except (remote.LoginError, virt_vm.VMError, aexpect.ShellError) as e:
            logging.error(str(e))
            test.error("Test transient disk shareable: login failed")

    vm_names = params.get("vms").split()
    if len(vm_names) < 2:
        test.cancel("No multi vms provided.")

    # Disk specific attributes.
    disk_bus = params.get("virt_disk_bus", "virtio")
    disk_target = params.get("virt_disk_target", "vdb")
    disk_type = params.get("virt_disk_type", "file")
    disk_device = params.get("virt_disk_device", "disk")
    disk_format = params.get("virt_disk_format", "qcow2")
    target_format = params.get("virt_target_format", "qcow2")
    hotplug = "yes" == params.get("virt_disk_vms_hotplug", "no")
    status_error = params.get("status_error").split()
    sharebacking = params.get("share_transient").split()
    # After libvirt 7.4.0, support for sharing base image of ``<transient/>``
    if not libvirt_version.version_compare(7, 4, 0):
        test.cancel("Sharing base image of transient disk is not supported for this libvirt version")
    disk_source_path = data_dir.get_data_dir()
    disk_path = ""

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
        image_size = params.get("image_size", "1G")
        disk_path = "%s/test.%s" % (disk_source_path, disk_format)
        disk_source = libvirt.create_local_disk("file", disk_path, image_size,
                                                disk_format=disk_format)
        disk_src_dict = {"attrs": {"file": disk_path}}
        disks.append({"format": disk_format,
                      "source": disk_source})

        # Compose the new domain xml
        for i in list(range(2)):
            vm = env.get_vm(vm_names[i])
            # Destroy domain first.
            if vm.is_alive():
                vm.destroy(gracefully=False)

            # Configure vm disk options and define vm
            vmxml = vm_xml.VMXML.new_from_dumpxml(vm_names[i])
            disk_xml = libvirt_disk.create_primitive_disk_xml(
                disk_type, disk_device, disk_target, disk_bus,
                target_format, disk_src_dict, None)

            if sharebacking[i] == "yes":
                disk_xml.sharebacking = "yes"
            else:
                disk_xml.transient = "yes"

            logging.debug("The disk xml is: %s" % disk_xml.xmltreefile)

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

                if i == 1:
                    check_transient_disk_keyword(vm_names)
                    check_share_transient_disk(vms_list)

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
            if "source" in img:
                os.remove(img["source"])
