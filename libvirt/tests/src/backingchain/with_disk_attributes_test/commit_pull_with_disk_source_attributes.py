import os

from virttest import data_dir
from virttest import virsh
from virttest import libvirt_version
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt

from provider.backingchain import blockcommand_base
from provider.backingchain import check_functions
from provider.virtual_disk import disk_base


def run(test, params, env):
    """
    Do blockcommit/blockpull with different attributes of source elements.
    1) Prepare a running guest with the following source elements and create snapshots:
    - datastore
    2) Do blockcommit/blockpull and check dumpxml.
    """

    def setup_test():
        """
        Prepare a running guest with different source elements and create snapshots.
        """
        test.log.info("Setup env: prepare a running guest and snap chain.")
        data_file = data_dir.get_data_dir() + '/datastore.img'
        data_file_option = params.get("data_file_option") % data_file
        back_obj.new_image_path = disk_obj.add_vm_disk(disk_type, disk_dict, new_image_path="",
                                                       extra=data_file_option)
        back_obj.backingchain_common_setup(create_snap=True, snap_num=snap_num)
        disk_xml = virsh.dumpxml(vm_name, "--xpath //disk", debug=True).stdout_text
        if data_file not in disk_xml:
            test.fail("Can't get the datastore element automatically!")
        return data_file, back_obj.new_image_path

    def run_test():
        """
        Do blockcommit/blockpull for the guest with different source attributes.
        """
        test.log.info("TEST_STEP1: Do blockcommit/blockpull.")
        if block_cmd == "blockcommit":
            virsh.blockcommit(vm_name, target_disk, common_options+blockcommit_options,
                              ignore_status=False, debug=True)
        if block_cmd == "blockpull":
            virsh.blockpull(vm_name, target_disk, common_options,
                            ignore_status=False, debug=True)
        test.log.info("TEST_STEP2: Check expected disk image.")
        expected_chain = back_obj.convert_expected_chain(expected_chain_index)
        check_obj.check_backingchain_from_vmxml(disk_type, target_disk, expected_chain)

    libvirt_version.is_libvirt_feature_supported(params)
    vm_name = params.get("main_vm")
    target_disk = params.get("target_disk")
    disk_type = params.get("disk_type")
    disk_dict = eval(params.get("disk_dict", "{}"))
    common_options = params.get("common_options")
    snap_num = int(params.get("snap_num"))
    block_cmd = params.get("block_cmd")
    blockcommit_options = params.get("blockcommit_options", "")
    expected_chain_index = params.get("expected_chain_index")

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    back_obj = blockcommand_base.BlockCommand(test, vm, params)
    check_obj = check_functions.Checkfunction(test, vm, params)
    disk_obj = disk_base.DiskBase(test, vm, params)

    try:
        data_file, new_image_path = setup_test()
        run_test()
    finally:
        libvirt.clean_up_snapshots(vm_name)
        bkxml.sync()
        for file in [data_file, new_image_path]:
            if os.path.exists(file):
                os.remove(file)
