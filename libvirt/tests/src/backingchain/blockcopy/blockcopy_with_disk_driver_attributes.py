from virttest import libvirt_version
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml

from provider.backingchain import blockcommand_base
from provider.virtual_disk import disk_base


def run(test, params, env):
    """
    Test Do blockcopy to target disk with different
    attributes of driver elements.

    1) Prepare an running guest.
    2) Do blockcopy to target disk with different attributes of
    driver elements.
    4) Check attrs
    """

    def setup_test():
        """
        Prepare active domain
        """
        test.log.info("TEST_SETUP: Start guest and prepare disk.")
        global xml_file
        xml_file = libvirt_vmxml.create_vm_device_by_type("disk", disk_dict).xml
        test_obj.backingchain_common_setup()

    def run_test():
        """
        Test blockcopy.
        """
        test.log.info("TEST_STEP1: Do blockcopy.")
        virsh.blockcopy(vm_name, device,
                        blockcopy_options.format(xml_file),
                        ignore_status=False,
                        debug=True)

        test.log.info("TEST_STEP2: Check driver elements.")
        pivot_byte_str = "<max_size unit='%s'>%s</max_size>" % (unit, max_size)
        libvirt_vmxml.check_guest_xml(vm_name, pivot_byte_str)

    def teardown_test():
        """
        Clean env
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        test_obj.clean_file(image_path)
        bkxml.sync()

    libvirt_version.is_libvirt_feature_supported(params)

    # Process cartesian parameters
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    device = params.get('target_disk')
    image_path = params.get('image_path')
    unit = params.get('unit')
    max_size = params.get('max_size')
    disk_dict = eval(params.get('disk_dict', '{}'))
    blockcopy_options = params.get('blockcopy_option')

    # Create object
    test_obj = blockcommand_base.BlockCommand(test, vm, params)
    disk_obj = disk_base.DiskBase(test, vm, params)

    # Get vm xml
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        # Execute test
        setup_test()
        run_test()

    finally:
        teardown_test()
