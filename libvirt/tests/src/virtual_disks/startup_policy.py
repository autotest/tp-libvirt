import os
import logging
import shutil
from autotest.client import utils
from autotest.client.shared import error
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest import virsh


def create_disk_xml(xml_file, device_type, source_file, target_dev, policy):
    """
    Create a disk xml file for attaching to a domain.

    :prams xml_file: path/file to save the disk XML
    :source_file: disk's source file
    :device_type: CD-ROM or floppy
    """
    if device_type == "cdrom":
        target_bus = "ide"
        image_size = "100M"
    elif device_type == "floppy":
        target_bus = "fdc"
        image_size = "1.44M"
    else:
        error.TestNAError("Unsupport device type in this test: " + device_type)
    utils.run("qemu-img create %s %s" % (source_file, image_size))
    disk_class = vm_xml.VMXML.get_device_class('disk')
    disk = disk_class(type_name='file')
    disk.device = device_type
    disk.driver = dict(name='qemu')
    disk.source = disk.new_disk_source(attrs={'file': source_file, 'startupPolicy': policy})
    disk.target = dict(bus=target_bus, dev=target_dev)
    disk.xmltreefile.write()
    shutil.copyfile(disk.xml, xml_file)


def check_disk_source(vm_name, target_dev, expect_value):
    """
    Check the disk source: file and startupPolicy.

    :params vm_name: Domain name
    :params target_dev: Disk's target device
    :params expect_value: Expect value of source file and source startupPolicy
    """
    logging.debug("Expect source file is '%s'", expect_value[0])
    logging.debug("Expect source startupPolicy is '%s'", expect_value[1])
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    disks = vmxml.get_disk_all()
    source_value = []
    try:
        disk_source = disks[target_dev].find('source')
        source_value.append(disk_source.get('file'))
        source_value.append(disk_source.get('startupPolicy'))
    except KeyError:
        raise error.TestError("No %s in domain %s" % (target_dev, vm_name))
    logging.debug("Actual source file is '%s'", source_value[0])
    logging.debug("Actual source startupPolicy is '%s'", source_value[1])
    if source_value == expect_value:
        logging.debug("Domain disk XML check pass")
    else:
        raise error.TestFail("Domain disk XML check fail")


def run(test, params, env):
    """
    Test startupPolicy for CD-ROM/floppy disks.

    Steps:
    1. Prapare disk media image.
    2. Setup startupPolicy for a disk.
    3. Start the domain.
    4. Save the diomian.
    5. Remove the disk source file and restore the domain.
    6. Recover the disk source file and restore the domain.
    10. Destroy the domain.
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    startup_policy = params.get("policy")

    if startup_policy == "mandatory":
        start_error = True
        restore_error = True
    elif startup_policy == "requisite":
        start_error = True
        restore_error = False
    elif startup_policy == "optional":
        start_error = False
        restore_error = False
    else:
        error.TestNAError("Unsupport startupPolicy ''%s' in this test" % startup_policy)

    # Back VM XML
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    # Create disk XML and attach it
    device_type = params.get("device_type")
    target_dev = params.get("target_dev")
    media_name = params.get("media_name")
    media_file = os.path.join(test.tmpdir, media_name)
    media_file_new = media_file + ".new"
    disk_xml_file = os.path.join(test.tmpdir, "attach_disk.xml")
    create_disk_xml(disk_xml_file, device_type, media_file, target_dev, startup_policy)
    virsh_dargs = {'debug': True, 'ignore_status': True}
    if vm.is_alive():
        vm.destroy()
    try:
        virsh.attach_device(domainarg=vm_name, filearg=disk_xml_file,
                            flagstr="--config", **virsh_dargs)
    except:
        os.remove(media_file)
        raise error.TestError("Attach %s fail", device_type)

    def rename_file(revert=False):
        """
        Rename a file or revert it.
        """
        try:
            if not revert:
                os.rename(media_file, media_file_new)
                logging.debug("Rename %s to %s", media_file, media_file_new)
            else:
                os.rename(media_file_new, media_file)
                logging.debug("Rename %s to %s", media_file_new, media_file)
        except OSError, err:
            raise error.TestFail("Rename image failed: %s" % str(err))

    save_file = os.path.join(test.tmpdir, "vm.save")
    expect_value = [None, startup_policy]
    try:
        # Step 1. Start domain and destroy it normally
        vm.start()
        vm.destroy()

        # Step 2. Remove the source_file then start the domain
        rename_file()
        result = virsh.start(vm_name, **virsh_dargs)
        libvirt.check_exit_status(result, expect_error=start_error)
        if not start_error:
            check_disk_source(vm_name, target_dev, expect_value)

        # Step 3. Move back the source file and start the domain(if needed)
        rename_file(revert=True)
        if not vm.is_alive():
            vm.start()

        # Step 4. Save the domain normally, then remove the source file
        # and restore it back
        vm.save_to_file(save_file)
        rename_file()
        result = virsh.restore(save_file, **virsh_dargs)
        libvirt.check_exit_status(result, expect_error=restore_error)
        if not restore_error:
            check_disk_source(vm_name, target_dev, expect_value)

        # Step 5. Move back the source file and restore the domain(if needed)
        rename_file(revert=True)
        if not vm.is_alive():
            result = virsh.restore(save_file, **virsh_dargs)
            libvirt.check_exit_status(result, expect_error=False)
    finally:
        vmxml_backup.sync()
        if os.path.exists(save_file):
            os.remove(save_file)
        if os.path.exists(disk_xml_file):
            os.remove(disk_xml_file)
        if os.path.exists(media_file):
            os.remove(media_file)
