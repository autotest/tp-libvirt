import logging

from avocado.utils import lv_utils

from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt

from provider.backingchain import blockcommand_base
from provider.backingchain import check_functions
from provider.virtual_disk import disk_base


LOG = logging.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Do blockcommit with all block disks in chain

    Scenarios:
    1) Do shallow inactive blockcommit .
    2) Do shallow active blockcommit .
    """
    def prepare_lvm():
        """
        Create lvm as snap path and convert to qcow2 format
        """
        path = []
        for i in range(1, lvm_num+1):
            source_path = '/dev/%s/%s' % (disk_obj.vg_name, lv_name+str(i))
            lv_utils.lv_create(vg_name, lv_name+str(i), vol_size)
            # Change lv to qcow2 format.
            libvirt.create_local_disk("file", source_path, vol_size,
                                      disk_format="qcow2")
            path.append(source_path)
        return path

    def setup_test():
        """
        Prepare specific type disk and create snapshots.
       """
        test_obj.new_image_path = disk_obj.add_vm_disk(disk_type, disk_dict)
        prepare_lvm()

        if not vm.is_alive():
            vm.start()
        vm.wait_for_login().close()

        test_obj.tmp_dir = "/dev/%s/%s" % (vg_name, lv_name)
        test_obj.prepare_snapshot(start_num=1, snap_num=snap_nums+1,
                                  option=snap_option, extra=extra_option)
        check_obj.check_backingchain_from_vmxml(disk_type, test_obj.new_dev,
                                                test_obj.snap_path_list[::-1] +
                                                [test_obj.new_image_path])

    def run_test():
        """
        Do blockcommit and check backingchain result
        """
        session = vm.wait_for_login()
        status, _ = session.cmd_status_output("which sha256sum")
        if status:
            test.error("Not find sha256sum command on guest.")
        ret, expected_md5 = session.cmd_status_output("sha256sum %s" %
                                                      "/dev/"+test_obj.new_dev)
        options = ''
        for i in range(1, commit_times+1):
            if test_scenario == "shallow_active":
                options = commit_options
            elif test_scenario == "shallow_inactive":
                options = commit_options % (test_obj.tmp_dir + str(i))

            LOG.debug("The %d times to do blockcommit", i)
            virsh.blockcommit(vm.name, test_obj.new_dev, options,
                              ignore_status=False, debug=True)

            expected_chain_index = params.get('expected_chain_%s' % i)
            expected_chain = test_obj.convert_expected_chain(
                expected_chain_index)
            check_obj.check_backingchain_from_vmxml(disk_type, test_obj.new_dev,
                                                    expected_chain)
        check_obj.check_hash_list(["/dev/"+test_obj.new_dev], [expected_md5], session)
        session.close()

    def teardown_test():
        """
        Clean data.
        """
        test_obj.backingchain_common_teardown()

        bkxml.sync()
        disk_obj.cleanup_disk_preparation(disk_type)

    vm_name = params.get("main_vm")
    disk_dict = eval(params.get('disk_dict', '{}'))
    commit_options = params.get('commit_options')
    test_scenario = params.get('test_scenario')
    snap_option = params.get('snap_option')
    extra_option = params.get('extra_option')
    disk_type = params.get('disk_type')
    commit_times = int(params.get('commit_times'))
    snap_nums = int(params.get('snap_nums'))
    lvm_num = int(params.get('lvm_num'))
    vg_name, lv_name, vol_size = 'vg0', 'lv', '200M'

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    # Create object
    test_obj = blockcommand_base.BlockCommand(test, vm, params)
    check_obj = check_functions.Checkfunction(test, vm, params)
    disk_obj = disk_base.DiskBase(test, vm, params)

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
