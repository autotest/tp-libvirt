import ast
import time
import os

from virttest import libvirt_version
from virttest import virsh
from virttest import utils_disk
from virttest import utils_misc
from virttest import remote
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_disk

from provider.virtual_disk import disk_base


def parse_rbd_disk_size(lines, image_name, test_logger):
    """
    Parse disk size from rbd du command output lines.

    :param lines (list): rbd du command output lines
    :param image_name (str): Name of the image to find
    :param test_logger: Test logger object
    :return: Disk size in MB
    """
    for line in lines:
        if image_name in line:
            parts = line.split()
            if len(parts) >= 5:
                try:
                    used_size_value = float(parts[3])
                    used_size_unit = parts[4]

                    # Convert to MB based on unit
                    if used_size_unit == "GiB":
                        disk_size = int(used_size_value * 1024)
                    elif used_size_unit == "MiB":
                        disk_size = int(used_size_value)
                    elif used_size_unit == "KiB":
                        disk_size = int(used_size_value / 1024)
                    elif used_size_unit == "B":
                        disk_size = int(used_size_value / (1024 * 1024))
                    else:
                        disk_size = int(used_size_value / (1024 * 1024))

                    test_logger.debug("Extracted used disk size: %s MB", disk_size)
                    return disk_size
                except (ValueError, IndexError):
                    test_logger.fail("Failed to parse disk size from rbd du output: %s" % parts[3])
            else:
                test_logger.fail("Insufficient data in rbd du output line: %s" % line)

    test_logger.fail("Image %s not found in rbd du output" % image_name)


def get_size_for_rbd(new_image_path, mon_host, server_user, server_pwd, test):
    """
    Get the used disk size for RBD image in MB.

    :param new_image_path: Path to the RBD image
    :param mon_host: Monitor host for Ceph cluster
    :param server_user: SSH username
    :param server_pwd: SSH password
    :param test: Test object for logging and error handling
    :return: Used disk size in MB
    """
    ceph_session = remote.wait_for_login("ssh", mon_host, "22", server_user,
                                         server_pwd, r"[\#\$]\s*$")
    try:
        rbd_du_cmd = "rbd du %s" % new_image_path
        rbd_du_output = ceph_session.cmd_output(rbd_du_cmd).strip()
        test.log.debug("RBD du output: %s", rbd_du_output)

        image_name = os.path.basename(new_image_path)
        lines = rbd_du_output.split('\n')
        return parse_rbd_disk_size(lines, image_name, test.log)
    finally:
        ceph_session.close()


def run(test, params, env):
    """
    Start guest with throttlefilters and check discard forwarding.
    """
    def prepare_guest():
        """
        Prepare the disk with throttlefilters.
        """
        for throttle_group in [throttlefilter_group1, throttlefilter_group2]:
            virsh.domthrottlegroupset(vm_name, throttle_group, options=throttle_options,
                                      debug=True, ignore_status=False)
        disk_obj.new_image_path = disk_obj.add_vm_disk(disk_type, disk_dict, size=disk_size)
        if not vm.is_alive():
            vm.start()
        vm.wait_for_login().close()
        test.log.debug("The current guest xml is: %s", virsh.dumpxml(vm_name).stdout_text)
        return disk_obj.new_image_path

    def get_disk_size(new_image_path):
        """
        Get the disk size inside guest.
        """
        disk_size = None
        if disk_type == "file":
            disk_info_dict = utils_misc.get_image_info(new_image_path)
            disk_size_bytes = disk_info_dict.get("dsize")
            if disk_size_bytes is None:
                test.fail("Failed to get image dsize for %s" % new_image_path)
            disk_size = int(disk_size_bytes) // (1024 * 1024)
        if disk_type == "rbd_with_auth":
            disk_size = get_size_for_rbd(new_image_path, mon_host, server_user, server_pwd, test)
        try:
            return int(disk_size)
        except (TypeError, ValueError):
            test.fail("Failed to get image dsize for %s" % new_image_path)

    def write_data(session):
        """
        Write data and discard data inside guest.
        """
        new_disk, _ = libvirt_disk.get_non_root_disk_name(session)
        mountpoint = utils_disk.configure_empty_linux_disk(session, new_disk, size=dd_count, fstype="xfs")
        utils_disk.dd_data_to_vm_disk(session, "%s/test" % mountpoint[0], count=dd_count)
        session.cmd_output("sync", timeout=240)
        return mountpoint

    def compare_disk_size():
        """
        Compare the disk size before and after writing data.
        """
        session = vm.wait_for_login()
        original_disk_size = get_disk_size(new_image_path)
        mountpoint = write_data(session)
        new_disk_size = get_disk_size(new_image_path)
        if (new_disk_size < write_threshold) or (new_disk_size <= original_disk_size):
            test.fail("Write operation did not increase the disk size to %s MB as expected." % new_disk_size)
        discard_command = "rm -rf %s/*; sync; fstrim -v %s" % (mountpoint[0], mountpoint[0])
        session.cmd_output(discard_command, timeout=240)
        test.log.info("Wait for 25 seconds to make sure the disk size has been discarded.")
        time.sleep(25)
        final_disk_size = get_disk_size(new_image_path)
        if (final_disk_size >= new_disk_size) or (final_disk_size >= discard_threshold):
            test.fail("Discard operation did not reduce the disk size %s MB as expected." % final_disk_size)
        else:
            test.log.info("Discard operation successfully reduced the disk size.")
        session.close()

    libvirt_version.is_libvirt_feature_supported(params)
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    disk_type = params.get("disk_type")
    disk_size = params.get("disk_size")
    write_threshold = int(params.get("write_threshold", "400"))
    discard_threshold = int(params.get("discard_threshold", "300"))
    disk_dict = ast.literal_eval(params.get("disk_dict", "{}"))
    throttlefilter_group1 = params.get("throttlefilter_group1")
    throttlefilter_group2 = params.get("throttlefilter_group2")
    throttle_options = params.get("throttle_options")
    dd_count = params.get("dd_count")
    check_qemu_pattern = params.get("check_qemu_pattern")
    server_user = params.get("server_user")
    server_pwd = params.get("private_key_password")
    mon_host = params.get("mon_host")

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    vmxml_backup = vmxml.copy()
    disk_obj = disk_base.DiskBase(test, vm, params)

    try:
        test.log.info("TEST_STEP: Prepare the guest.")
        new_image_path = prepare_guest()

        test.log.info("TEST_STEP: Check the qemu command line.")
        libvirt.check_qemu_cmd_line(check_qemu_pattern)

        test.log.info("TEST_STEP: Login the guest, write/discard data then compare disk size.")
        compare_disk_size()

    finally:
        if vm.is_alive():
            vm.destroy()
        vmxml_backup.sync()
        disk_obj.cleanup_disk_preparation(disk_type)
