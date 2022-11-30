import re

from avocado.utils import process

from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_secret
from virttest.utils_libvirt import libvirt_vmxml

from provider.backingchain import blockcommand_base
from provider.backingchain import check_functions
from provider.virtual_disk import disk_base


def run(test, params, env):
    """
    Do blockcopy with --xml option to destination image with luks and slices.

    1) Prepare disk
        disk types: file, block
    2) Do blockcopy:
        with --finish and --reuse-external
        with --pivot and --reuse-external
    3) Check result
    """

    def setup_test():
        """
        Prepare running domain, slice image and dest image.
        """
        test.log.info("TEST_SETUP:Setup env.")
        libvirt.create_local_disk(disk_type="file", extra=file_dest_extra,
                                  path=file_dest_path, size=image_size,
                                  disk_format=file_dest_format)
        du_output = process.run("du -b %s | cut -f1" % file_dest_path,
                                shell=True).stdout_text
        global actual_size
        actual_size = re.findall(r'[0-9]+', du_output)[0]

        test.log.debug("Format device with luks")
        dest_path = get_common_dest_path(file_dest_path)
        test_obj.new_image_path = dest_path

        test.log.debug("Prepare secret")
        sec_uuid = libvirt_secret.create_secret(sec_dict=sec_dict)

        virsh.secret_set_value(sec_uuid, secret_pwd, debug=True)

        test.log.debug("Create source disk xml")
        libvirt.create_local_disk(disk_type="file",
                                  path=src_disk_path, size=image_size,
                                  disk_format=src_disk_format)
        disk_obj.add_vm_disk(source_disk_type, source_disk_dict,
                             new_image_path=src_disk_path)
        test_obj.backingchain_common_setup()

        test.log.debug("Create dest disk xml")
        create_dest_xml(dest_path, sec_uuid, slice_offset, actual_size)

    def run_test():
        """
        1) Do blockcopy with --xml option to destination image
        with luks and slices.
        2) Check backingchain list and slice offset and size
        """
        test.log.info("TEST_STEP1: Do blockcopy")
        virsh.blockcopy(vm_name, target_disk,
                        blockcopy_options.format(xml_file),
                        debug=True, ignore_status=False)

        test.log.info("TEST_STEP2: Check backingchain and slice value")
        check_result()

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        test_obj.clean_file(file_dest_path)
        test_obj.clean_file(src_disk_path)

        if dest_disk_type == "block":
            libvirt.setup_or_cleanup_iscsi(is_setup=False)

        bkxml.sync()
        libvirt_secret.clean_up_secrets()

    def get_common_dest_path(file_dest_path):
        """
        Get common dest path

        :param file_dest_path: The file path based qcow2 image with luks.
        """
        dest_path = ''
        if dest_disk_type == "block":
            dest_path = libvirt.setup_or_cleanup_iscsi(is_setup=True)

            cmd = "qemu-img convert -f %s -O %s %s %s" % (
                blk_dest_format, blk_dest_format, file_dest_path, dest_path)
            process.run(cmd, shell=True, ignore_status=False)

        elif dest_disk_type == "file":
            dest_path = file_dest_path

        return dest_path

    def create_dest_xml(dest_path, sec_uuid, offset, size):
        """
        Prepare the destination xml

        :param dest_path: xml source path.
        :param offset: offset value
        :param sec_uuid: secret uuid
        :param size: size value
        """
        source_key = ''
        dest_disk_dict = eval(params.get("dest_disk_dict", "{}"))

        if dest_disk_type == "file":
            dest_disk_dict = source_disk_dict
            source_key = "file"
        elif dest_disk_type == "block":
            source_key = "dev"

        dest_disk_dict.update(
            {"source":  {"attrs": {source_key: dest_path},
             "encryption": {"secret": {"type": "passphrase", "uuid": sec_uuid},
                            "encryption": "luks"},
                         "slices": {"slice_type": "storage",
                                    "slice_offset": offset,
                                    "slice_size": str(size)}}})
        dest_xml = libvirt_vmxml.create_vm_device_by_type("disk",
                                                          dest_disk_dict)
        test.log.debug("Current dest xml is:%s" % dest_xml)

        global xml_file
        xml_file = dest_xml.xml

        return xml_file

    def check_result():
        """
        Check backingchain list and slice offset and size
        """
        if operation == "finish":
            check_obj.check_backingchain_from_vmxml("file", target_disk,
                                                    [src_disk_path])
        elif operation == "pivot":
            check_obj.check_backingchain_from_vmxml(dest_disk_type, target_disk,
                                                    [test_obj.new_image_path])

            slice_str = "<slice type='storage' offset='%s' size='%s'/>" %\
                        (slice_offset, actual_size)
            libvirt_vmxml.check_guest_xml(vm_name, slice_str)

    # Process cartesian parameters
    vm_name = params.get("main_vm")
    target_disk = params.get('target_disk')
    source_disk_type = params.get('source_disk_type')
    dest_disk_type = params.get('dest_disk_type')
    source_disk_dict = eval(params.get("source_disk_dict", "{}"))
    sec_dict = eval(params.get('sec_dict'))
    secret_pwd = params.get('secret_pwd')
    blk_dest_format = params.get('blk_dest_format')
    operation = params.get('operation')

    image_size = params.get('image_size')
    slice_offset = params.get('slice_offset')
    file_dest_extra = params.get('file_dest_extra')
    file_dest_format = params.get('file_dest_format')
    file_dest_path = params.get('file_dest_path')
    src_disk_path = params.get('src_disk_path')
    src_disk_format = params.get('src_disk_format')
    blockcopy_options = params.get('blockcopy_options')

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    # Create object
    test_obj = blockcommand_base.BlockCommand(test, vm, params)
    check_obj = check_functions.Checkfunction(test, vm, params)
    disk_obj = disk_base.DiskBase(test, vm, params)

    if "EXAMPLE_PWD" in secret_pwd:
        test.error("secret_pwd should be replaced by actual password")
    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
