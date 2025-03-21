import re
import os

from virttest import virsh
from virttest import libvirt_version
from virttest import utils_disk
from virttest import data_dir

from virttest.libvirt_xml import vm_xml
from avocado.utils import process
from virttest.utils_libvirt import libvirt_vmxml, libvirt_disk
from virttest.utils_config import LibvirtQemuConfig
from virttest.utils_libvirtd import Libvirtd

from provider.virtual_disk import disk_base


def run(test, params, env):
    """
    Verify start guest and hotplug/unplug ssh network disk.

    1. Prepare disk xml with ssh network disk.
    2. Start guest with or without network disk based on scenarios.
    3. Hotplug network disk only for hotplugging test scenario.
    4. Check disk in guest.
    5. Hot-unplug network disk if necessary.
    """
    def setup_test():
        """
        Prepare a ssh network disk test environment.

        :params qemu_config: return the qemu config.
        """
        qemu_config = LibvirtQemuConfig()
        qemu_config.storage_use_nbdkit = 1
        Libvirtd('virtqemud').restart()
        return qemu_config

    def check_result(disk_in_vm=True):
        """
        Check the dumpxml and the nbdkit process.

        :params disk_in_vm: boolean value to make sure if disk in vm.
        """
        test.log.info("TEST_STEP: Check the nbdkit process.")
        cmd = "ps aux | grep nbdkit | grep -v grep"
        nbdkit_process = process.run(cmd, shell=True, ignore_status=True).stdout_text
        match_result = re.findall(r"storage.socket", nbdkit_process)
        if disk_in_vm and not match_result:
            test.fail("Can't find the nbdkit process!")
        if not disk_in_vm:
            if match_result:
                test.fail("Find the nbdkit process unexpectedly!")
            else:
                test.log.debug("Can't find nbdkit process after hot-unplugging as expect!")
                return

        test.log.info("TEST_STEP: Check the vm xml.")
        if with_secret_passwd:
            expected_xpaths = eval(params.get("expected_xpaths") %
                                   known_hosts_path)
        else:
            expected_xpaths = eval(params.get("expected_xpaths") %
                                   (known_hosts_path, keyfile))
        xml_after_adding_device = vm_xml.VMXML.new_from_dumpxml(vm_name)
        test.log.debug("The current guest xml is: %s", xml_after_adding_device)
        libvirt_vmxml.check_guest_xml_by_xpaths(xml_after_adding_device, expected_xpaths)

        test.log.info("TEST_STEP: Do read/write for disk.")
        vm_session = vm.wait_for_login()
        new_disk = libvirt_disk.get_non_root_disk_name(vm_session)[0]
        utils_disk.dd_data_to_vm_disk(vm_session, new_disk)
        vm_session.close()

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    disk_type = params.get("disk_type")
    disk_dict = eval(params.get("disk_dict", "{}"))
    with_hotplug = "yes" == params.get("with_hotplug", "no")
    with_secret_passwd = "yes" == params.get("with_secret_passwd", "no")
    libvirt_version.is_libvirt_feature_supported(params)
    keyfile = os.path.join(data_dir.get_tmp_dir(), "keyfile")
    known_hosts_path = os.path.join(data_dir.get_tmp_dir(), "known_hosts")
    kwargs = {"keyfile": keyfile, "known_hosts_path": known_hosts_path}

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm.name)
    bkxml = vmxml.copy()
    disk_obj = disk_base.DiskBase(test, vm, params)

    try:
        qemu_config = setup_test()
        if not with_hotplug:
            disk_obj.new_image_path = disk_obj.add_vm_disk(disk_type,
                                                           disk_dict,
                                                           **kwargs)

        test.log.info("TEST_STEP: Start the guest.")
        if not vm.is_alive():
            virsh.start(vm_name, debug=True, ignore_status=False)
        if with_hotplug:
            test.log.info("TEST_STEP: Hotplug the disk to guest.")
            disk_dev, disk_obj.new_image_path = \
                disk_obj.prepare_disk_obj(disk_type, disk_dict, **kwargs)
            virsh.attach_device(vm_name, disk_dev.xml, debug=True,
                                ignore_status=False, wait_for_event=True)
        check_result()

        if with_hotplug:
            test.log.info("TEST_STEP: Hot-unplug the disk from guest.")
            virsh.detach_device(vm_name, disk_dev.xml, debug=True,
                                ignore_status=False, wait_for_event=True)
            check_result(disk_in_vm=False)
    finally:
        qemu_config.restore()
        Libvirtd('virtqemud').restart()
        bkxml.sync()
        disk_obj.cleanup_disk_preparation(disk_type, **kwargs)
