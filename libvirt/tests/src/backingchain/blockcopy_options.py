import logging
import os

from avocado.utils import process

from virttest import libvirt_version
from virttest import utils_disk
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_disk
from virttest.utils_libvirt import libvirt_secret
from virttest.utils_test import libvirt


from provider.backingchain import blockcommand_base
from provider.backingchain import check_functions

LOG = logging.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test blockcopy with different options.

    1) Prepare an running guest.
    2) Create snap.
    3) Do blockcopy.
    4) Check status by 'qemu-img info'.
    """

    def setup_blockcopy_extended_l2():
        """
        Prepare running domain with extended_l2=on type image.
        """
        # prepare image
        image_path = test_obj.tmp_dir + '/new_image'

        libvirt.create_local_disk("file", path=image_path, size='500M',
                                  disk_format=disk_format, extra=disk_extras)
        check_obj.check_image_info(image_path, check_item='extended l2',
                                   expected_value='true')
        test_obj.new_image_path = image_path
        # start get old parts
        session = vm.wait_for_login()
        session.close()
        # attach new disk
        if encryption_disk:
            secret_disk_dict = eval(params.get("secret_disk_dict", '{}'))
            test_obj.prepare_secret_disk(image_path, secret_disk_dict)
            if not vm.is_alive():
                vm.start()
        else:
            virsh.attach_disk(vm.name, source=image_path, target=device,
                              extra=attach_disk_extra, debug=True,
                              ignore_status=False)
        test_obj.new_dev = device
        # clean copy file
        if os.path.exists(tmp_copy_path):
            process.run('rm -f %s' % tmp_copy_path)

    def test_blockcopy_extended_l2():
        """
        Do blockcopy after creating snapshot with extended_l2 in disk image
        """
        # create snap chain and check snap path extended_l2 status
        test_obj.prepare_snapshot(snap_num=1)
        check_obj.check_image_info(test_obj.snap_path_list[0],
                                   check_item='extended l2',
                                   expected_value='true')
        # Do blockcopy
        virsh.blockcopy(vm_name, device, tmp_copy_path, options=blockcopy_options,
                        ignore_status=False, debug=True)
        # Check domain exist blockcopy file and extended_l2 status
        if len(vmxml.get_disk_source(vm_name)) < 2:
            test.fail('Domain disk num is less than 2, may attach failed')
        else:
            image_file = vmxml.get_disk_source(vm_name)[1].find('source').get('file')
            if image_file != tmp_copy_path:
                test.fail('Blockcopy path is not in domain disk ,'
                          ' blockcopy image path is %s ,actual image path '
                          'is :%s', tmp_copy_path, image_file)
            check_obj.check_image_info(tmp_copy_path, check_item='extended l2',
                                       expected_value='true')
        # Check domain write file
        session = vm.wait_for_login()
        added_disk, _ = libvirt_disk.get_non_root_disk_name(session)
        utils_disk.linux_disk_check(session, added_disk)
        session.close()

    def teardown_blockcopy_extended_l2():
        """
        Clean env.
        """
        if encryption_disk:
            libvirt_secret.clean_up_secrets()
        test_obj.backingchain_common_teardown()
        # detach disk
        virsh.detach_disk(vm_name, target=device, wait_for_event=True,
                          debug=True)
        process.run('rm -f %s' % test_obj.new_image_path)

    libvirt_version.is_libvirt_feature_supported(params)

    # Process cartesian parameters
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    case_name = params.get('case_name', '')
    device = params.get('target_disk')
    disk_extras = params.get('extras_options')
    blockcopy_options = params.get('blockcopy_option')
    attach_disk_extra = params.get("attach_disk_options")
    encryption_disk = params.get('enable_encrypt_disk', 'no') == "yes"
    disk_format = params.get('disk_format', 'qcow2')
    # Create object
    test_obj = blockcommand_base.BlockCommand(test, vm, params)
    check_obj = check_functions.Checkfunction(test, vm, params)
    # Get vm xml
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    test_obj.original_disk_source = libvirt_disk.get_first_disk_source(vm)
    tmp_copy_path = os.path.join(os.path.dirname(
        libvirt_disk.get_first_disk_source(vm)), "%s_blockcopy.img" % vm_name)
    # MAIN TEST CODE ###
    run_test = eval("test_%s" % case_name)
    setup_test = eval("setup_%s" % case_name)
    teardown_test = eval("teardown_%s" % case_name)

    try:
        # Execute test
        setup_test()
        run_test()

    finally:
        teardown_test()
        bkxml.sync()
