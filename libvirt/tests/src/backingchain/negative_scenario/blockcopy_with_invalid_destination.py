import os

from avocado.utils import process

from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_disk
from virttest.utils_test import libvirt

from provider.backingchain import blockcommand_base


def run(test, params, env):
    """
    Test blockcopy with invalid destination file.

    1) Prepare an running guest.
    3) Do blockcopy with invalid destination file
    4) Check result.
    """

    def setup_test():
        """
        Prepare active guest.
        """
        test.log.info("TEST_SETUP:Start vm and clean exist copy file")
        test_obj.backingchain_common_setup(remove_file=True,
                                           file_path=tmp_copy_path)

    def test_not_exist():
        """
        Do blockcopy with --reuse-external option when the destination file
        is not exist.
        """
        test.log.info("TEST_STEP1: Do blockcopy with non-exist file")
        ret = virsh.blockcopy(vm_name, target_disk, blockcopy_options,
                              debug=True)
        libvirt.check_result(ret, expected_fails=expected_err)

    def test_less_image():
        """
        Do blockcopy with --reuse-external option when the space of
        destination file is less than image.
        """
        test.log.info("TEST_STEP1: Do blockcopy with less size image")
        libvirt.create_local_disk("file", size=image_size, path=tmp_copy_path,
                                  disk_format=image_format)
        ret = virsh.blockcopy(vm_name, target_disk,
                              blockcopy_options % tmp_copy_path,
                              debug=True)
        libvirt.check_result(ret, expected_fails=expected_err,
                             check_both_on_error=True)

    def test_relative_path():
        """
        Do blockcopy with different relative path in
        destination file: "image", "./image", "../image".
        """
        test.log.info("TEST_STEP1: Do blockcopy with relative path")
        ret = virsh.blockcopy(vm_name, target_disk, image_path,
                              options=blockcopy_options,
                              debug=True)
        libvirt.check_result(ret, expected_fails=expected_err)

    def test_absolute_path():
        """
        Do blockcopy with absolute path, and destroy guest during blockcopy
        than check the destination file.
        """
        test.log.info("TEST_STEP1: Do blockcopy and destroy guest during copy")
        cmd = blockcopy_options % (vm_name, tmp_copy_path)
        virsh_session = virsh.VirshSession(virsh_exec=virsh.VIRSH_EXEC,
                                           auto_close=True)
        virsh_session.sendline(cmd)
        test.log.info('Blockcopy cmd :%s', cmd)

        test.log.info("TEST_STEP2: Get file xattr ")
        _check_result()

    def teardown_test():
        """
        Clean env
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        bkxml.sync()
        test_obj.clean_file(tmp_copy_path)
        test_obj.clean_file(test_obj.new_image_path)

    def _check_xattr(image, check_item, status_error=False):
        """
        Check the xattr of the destination file.

        :param image: image should checked.
        :param check_item: The item you want to checked.
        :param status_error: Set to True if you expect the test to fail.
        """
        cmd = "getfattr -n trusted.libvirt.security.ref_selinux %s" % image
        ret = process.run(cmd, shell=True, ignore_status=True)
        if status_error:
            libvirt.check_result(ret, expected_fails=check_item,
                                 check_both_on_error=True)
        else:
            libvirt.check_result(ret, expected_match=check_item)

    def _check_result():
        """
        Destroy vm during blockcopy process, and check the destination file.

        1)Get the xattr of the destination file.
        2)Destroy guest
        3)Check the xattr of the destination file.
        """
        if not libvirt.check_blockjob(vm_name, target_disk, "progress", "100"):
            _check_xattr(tmp_copy_path, msg_before_destroy)

            test.log.info("TEST_STEP3: Destroy guest and check image xattr "
                          "again after destroy ")
            ret = virsh.destroy(vm_name)
            libvirt.check_exit_status(ret)
            _check_xattr(tmp_copy_path, expected_err, status_error=True)

        else:
            test.error("Please reset the blockcopy bandwidth, than test "
                       "before blockcopy over")

    # Process cartesian parameters
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    case = params.get("case")
    target_disk = params.get("target_disk")
    image_size = params.get("image_size")
    image_format = params.get("image_format")
    blockcopy_options = params.get("blockcopy_options")
    expected_err = params.get("expected_err")
    msg_before_destroy = params.get("before_destroy")
    image_path = params.get("image_path")

    test_obj = blockcommand_base.BlockCommand(test, vm, params)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    tmp_copy_path = os.path.join(os.path.dirname(
        libvirt_disk.get_first_disk_source(vm)), "%s_blockcopy.img" % vm_name)

    run_test = eval("test_%s" % case)

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
