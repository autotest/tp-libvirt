import time

from avocado.utils import lv_utils

from virttest import data_dir
from virttest import utils_disk
from virttest import utils_misc
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_misc
from virttest.utils_test import libvirt

from provider.backingchain import blockcommand_base
from provider.virtual_disk import disk_base


def run(test, params, env):
    """
    Check for the allocation watermark by domstats
    during blockcommit - BZ1041569

    Matrix:
    1) file disk: disk image mainly in NFS server
       block disk: disk image mainly in iSCSI server
    2) from inactive layer
       from active layer.
    """

    def setup_test():
        """
        Set disk
        """
        test_obj.new_image_path = disk_obj.add_vm_disk(
            disk_type, disk_dict)
        test_obj.backingchain_common_setup()

    def run_test():
        """
        Check for the allocation watermark by domstats
        """
        test.log.info("TEST_STEP1: Check allocation will keep changing "
                      "during create file in guest after snap.")
        path_list = prepare_snap_path()

        format_disk = True
        for index in range(0, snap_nums):
            test_obj.prepare_snapshot(snap_name="snap_%s" % index,
                                      snap_num=1, extra=extra_option,
                                      snap_path=path_list[index],
                                      clean_snap_file=False)

            alloc_new_dict_1 = get_alloc_result()
            write_file(format_disk, "/mnt/file_%s" % index)
            format_disk = False
            alloc_new_dict_2 = get_alloc_result()
            if alloc_new_dict_1 == alloc_new_dict_2:
                test.fail("block.x.allocation should keep changing during "
                          "create file in guest.")

        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        test_obj.snap_path_list = disk_obj.get_source_list(vmxml, disk_type,
                                                           test_obj.new_dev)
        test.log.debug("After creating snaps, The xml is:\n%s", vmxml)

        # Manual case requires to sleep 5 seconds and check the domstats again
        time.sleep(5)
        test.log.info("TEST_STEP2: Check %s keep changing "
                      "during blockcommit.", disappear_alloc)
        cmd = "blockcommit %s %s %s " % (vm_name, target_disk,
                                         commit_option)
        virsh_session = virsh.VirshSession(virsh_exec=virsh.VIRSH_EXEC,
                                           auto_close=True)
        virsh_session.sendline(cmd)
        test.log.debug("blockcommit cmd: %s", cmd)

        alloc_dict = {}
        for i in range(0, 2):
            alloc_new_dict = get_alloc_result()
            test.log.debug('Current alloc dict is:%s, last alloc dict is :%s',
                           alloc_new_dict, alloc_dict)
            if alloc_new_dict.get(disappear_index) == \
                    alloc_dict.get(disappear_index, ""):
                test.fail("%s should keep changing during blockcommit %s." % (
                    disappear_alloc, virsh_session.get_stripped_output()))
            alloc_dict = alloc_new_dict

        test.log.info("TEST_STEP3: Check %s should disappear" % disappear_alloc)
        if utils_misc.wait_for(lambda: commit_success_msg in
                               virsh_session.get_stripped_output(), 40, step=0.01):
            alloc_new_dict = get_alloc_result()
            if disappear_alloc in alloc_new_dict:
                test.fail("The % should not be in %s" % (disappear_alloc,
                                                         alloc_new_dict))
        else:
            test.log.debug("Current commit result:%s ",
                           virsh_session.get_stripped_output())
            test.fail("Blockcommit still not finish in 40s, "
                      "Please control commit speed to test :%s",
                      (virsh_session.get_stripped_output()))

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        test_obj.backingchain_common_teardown()

        bkxml.sync()
        disk_obj.cleanup_disk_preparation(disk_type)

    def prepare_snap_path():
        """
        Create snap path
        """
        path = []
        if disk_type == "block":
            for item in range(1, snap_nums + 1):
                source_path = '/dev/%s/%s' % (
                    disk_obj.vg_name, lv_name + str(item))
                lv_utils.lv_create(vg_name, lv_name + str(item), lv_size)
                # Change lv to qcow2 format.
                libvirt.create_local_disk("file", source_path, lv_size,
                                          disk_format="qcow2")
                path.append(source_path)
        elif disk_type == "nfs":
            for item in range(0, snap_nums):
                source_path = data_dir.get_data_dir() + "/images/snap_%s" % (
                    item)
                path.append(source_path)
        return path

    def write_file(format_disk, file_name):
        """
        Write file to target disk

        :param: format_disk: only format disk for one time in vm
        :param: file_name: file name
        """
        session = vm.wait_for_login()
        if format_disk:
            cmd = "mkfs.ext4 /dev/%s;mount /dev/%s /mnt" % (target_disk, target_disk)
            session.cmd_status_output(cmd)
        utils_disk.dd_data_to_vm_disk(session, file_name, bs='1M',
                                      count='50')
        session.close()

    def get_alloc_result():
        """
        Get block.x.allocation value from virsh.domstats result

        :return: alloc_dict: Return {"0":"1129906176", "1":"196616"} if get
        'block.0.allocation=1129906176', 'block.1.allocation=196616'
        """
        domstats_result = virsh.domstats(vm_name, domstats_option,
                                         ignore_status=True, debug=True).stdout
        alloc_dict = libvirt_misc.convert_to_dict(
            domstats_result.strip("\n"), pattern=r"block.(\d+).allocation=(\d+)")
        test.log.debug("Got alloc dict :%s", alloc_dict)

        return alloc_dict

    vm_name = params.get("main_vm")
    disk_dict = eval(params.get('disk_dict', '{}'))
    target_disk = params.get('target_disk')
    commit_option = params.get('commit_option')
    disappear_index = params.get('disappear_index')
    disappear_alloc = params.get('disappear_alloc')
    commit_success_msg = params.get('commit_success_msg')
    extra_option = params.get('extra_option')
    disk_type = params.get('disk_type')
    domstats_option = params.get('domstats_option')
    snap_nums = int(params.get('snap_nums'))
    vg_name, lv_name, lv_size = 'vg0', 'lv', "200M"

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    test_obj = blockcommand_base.BlockCommand(test, vm, params)
    disk_obj = disk_base.DiskBase(test, vm, params)

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
