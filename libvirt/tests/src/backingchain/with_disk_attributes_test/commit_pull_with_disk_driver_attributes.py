from virttest import virsh
from virttest.libvirt_xml import snapshot_xml
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.backingchain import blockcommand_base


def run(test, params, env):
    """
    Do blockcommit/blockpull with different attributes of driver elements.
    1) Prepare a running guest with the follow driver elements and create snapshots:
    - copy_on_read='on'
    - cache and discard
    - detect_zeroes
    - metadata_cache ->max_size
    2) Do blockcommit/blockpull and check dumpxml.
    """

    def setup_test():
        """
        Prepare a running guest with different driver elements and create snapshots.
        """
        test.log.info("Setup env: prepare a running guest and snap chain.")
        if block_cmd == "blockpull" and with_metadata_cache:
            test_obj.backingchain_common_setup()
            prepare_snapshot()
        else:
            libvirt_vmxml.modify_vm_device(vmxml, 'disk', driver_dict)
            test_obj.backingchain_common_setup(create_snap=True, snap_num=snap_num)

    def run_test():
        """
        Do blockcommit/blockpull for the guest with different driver attributes
        """
        test.log.info("TEST_STEP1: Do blockcommit/blockpull")
        if block_cmd == "blockcommit":
            virsh.blockcommit(vm_name, target_disk, common_options+blockcommit_options,
                              ignore_status=False, debug=True)
        elif block_cmd == "blockpull":
            virsh.blockpull(vm_name, target_disk, common_options,
                            ignore_status=False, debug=True)
        test.log.info("TEST_STEP2: Check driver attributes in dumpxml.")
        check_attributes()

    def teardown_test():
        """
        Clean up the test environment.
        """
        # Clean up the snapshots
        libvirt.clean_up_snapshots(vm_name)
        bkxml.sync()

    def prepare_snapshot():
        """
        Create a snapshot with snapshot xml
        """
        snap_xml = snapshot_xml.SnapshotXML()
        disk_obj = snap_xml.SnapDiskXML()
        disk_obj.setup_attrs(**snap_disk_dict)
        snap_xml.set_disks([disk_obj])
        snap_file = snap_xml.xml
        snap_options = " %s %s" % (snap_file, snap_option)
        snapshot_result = virsh.snapshot_create(vm_name, snap_options,
                                                ignore_status=False, debug=True)

    def check_attributes():
        """
        Check the disk attributes in dumpxml
        """
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        test.log.debug("The guest xml is: %s" % vmxml)
        disk = vmxml.get_devices("disk")[0]
        cur_dict = disk.fetch_attrs()[driver_element]
        pre_dict = driver_dict[driver_element]
        for key, value in pre_dict.items():
            if cur_dict.get(key) != value:
                test.fail("Driver XML compare fails. It should be '%s', but "
                          "got '%s'" % (pre_dict, cur_dict))
        else:
            test.log.debug("Can get the expected attributes '%s'" % cur_dict)

    # Process cartesian parameters
    vm_name = params.get("main_vm")
    target_disk = params.get('target_disk')
    disk_type = params.get("disk_type")
    common_options = params.get("common_options")
    snap_num = int(params.get("snap_num"))
    block_cmd = params.get("block_cmd")
    blockcommit_options = params.get("blockcommit_options", "")
    snap_option = params.get("snap_option")
    snap_disk_dict = eval(str(params.get("snap_disk_dict", "{}")))
    driver_dict = eval(params.get("driver_dict", "{}"))
    with_metadata_cache = params.get("with_metadata_cache", "no") == "yes"
    driver_element = params.get("driver_element")

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    test_obj = blockcommand_base.BlockCommand(test, vm, params)

    try:
        setup_test()
        run_test()
    finally:
        teardown_test()
