from virttest import data_dir
from virttest import libvirt_version
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt

from provider.backingchain import blockcommand_base
from provider.backingchain import check_functions
from provider.virtual_disk import disk_base


def run(test, params, env):
    """
    Test blockcopy to target disk with different dest xml
    1) Prepare an running guest and different dest xml.
    2) Do blockcopy with xml
    4) Check result
    """

    def setup_test():
        """
        Prepare disk and dest xml
        """
        test.log.info("TEST_SETUP: Prepare disk and dest xml.")
        test_obj.new_image_path = disk_obj.add_vm_disk(
            source_disk_type, source_disk_dict, size=image_size)
        test_obj.backingchain_common_setup()

        if dest_disk_type == "file":
            dest_image = data_dir.get_data_dir() + "/images/dest.img"
            libvirt.create_local_disk("file", path=dest_image,
                                      size=image_size, disk_format="qcow2")
        else:
            dest_image = ''
        global dest_obj, new_xml_path
        dest_obj, new_xml_path = disk_obj.prepare_disk_obj(
            dest_disk_type, dest_disk_dict, new_image_path=dest_image,
            size=image_size)

    def run_test():
        """
        Test blockcopy.
        """
        test.log.info("TEST_STEP1: Do blockcopy with dest xml.")
        virsh.blockcopy(vm_name, target_disk,
                        blockcopy_option.format(dest_obj.xml),
                        ignore_status=False,
                        debug=True)

        test.log.info("TEST_STEP2: Check result and backingchain.")
        if operation == "finish":
            check_obj.check_backingchain_from_vmxml(dest_disk_type, target_disk,
                                                    [test_obj.new_image_path])
        elif operation == "pivot":
            if dest_disk_type == "nbd":
                expected_path = export_name
            else:
                expected_path = new_xml_path

            check_obj.check_backingchain_from_vmxml(dest_disk_type, target_disk,
                                                    [expected_path])

    def teardown_test():
        """
        Clean env
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        test_obj.backingchain_common_teardown()
        bkxml.sync()

        disk_obj.cleanup_disk_preparation(dest_disk_type)
        disk_obj.cleanup_disk_preparation(source_disk_type)

    libvirt_version.is_libvirt_feature_supported(params)

    # Process cartesian parameters
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    target_disk = params.get('target_disk')
    operation = params.get('operation')
    image_size = params.get('image_size')
    export_name = params.get('export_name', '')
    dest_disk_type = params.get('dest_disk_type')
    source_disk_type = params.get('source_disk_type')
    source_disk_dict = eval(params.get('source_disk_dict', '{}'))
    dest_disk_dict = eval(params.get('dest_disk_dict', '{}'))
    blockcopy_option = params.get('blockcopy_option')

    # Create object
    test_obj = blockcommand_base.BlockCommand(test, vm, params)
    disk_obj = disk_base.DiskBase(test, vm, params)
    check_obj = check_functions.Checkfunction(test, vm, params)

    # Get vm xml
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
