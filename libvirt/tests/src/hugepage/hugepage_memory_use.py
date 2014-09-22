import logging
from autotest.client.shared import error
from virttest import virt_vm
from virttest.libvirt_xml.vm_xml import VMXML
from virttest import utils_libvirtd, utils_misc
from virttest.staging import utils_memory


def run(test, params, env):
    """
    Test steps:

    1) Get the params from params.
    2) Strat the VM and check whether the VM been started successfully
    3) Compare the Hugepage memory size to the Guest memory setted.
    4) Check the hugepage memory usage.
    5) Clean up
    """

    hugepage_num = int(params.get("hugepages_start_nr_hugepage_num", 0))
    thp_status = utils_memory.get_transparent_hugepage()
    thp_enable = ('yes' == params.get("transparent_hugepage_enable", 'yes'))

    hugepage_tag = ('yes' == params.get("vm_hugepage_tag", 'yes'))
    compare_to_mem = ('yes' == params.get("compare_to_memory", 'no'))
    mem_range = float(params.get("mem_error_range", 1.1))
    # backup vm.xml
    vm_name = params.get("main_vm")
    back_xml = VMXML.new_from_inactive_dumpxml(vm_name)
    try:
        try:
            if utils_misc.is_mounted("hugetlbfs", "/dev/hugepages",
                                     "hugetlbfs") is not True:
                mount_flag = "no"
                # Mount hugetlbfs on /dev/hugepages
                utils_misc.mount("hugetlbfs", "/dev/hugepages", "hugetlbfs")
            else:
                mount_flag = "yes"
            utils_memory.set_num_huge_pages(hugepage_num)
            utils_memory.set_transparent_hugepage(thp_enable)
            # Set the VM with hugepage tag
            if hugepage_tag:
                back_xml = utils_memory.set_hugepage_tag(vm_name)
                utils_libvirtd.libvirtd_restart()
            else:
                back_xml = VMXML.new_from_inactive_dumpxml(vm_name)
            vm = env.get_vm(vm_name)
            #import pdb
            # pdb.set_trace()
            hugepage_before_start = utils_memory.get_num_huge_pages()
            logging.debug("hugepage_before_start : %d" % hugepage_before_start)
            if utils_misc.is_mounted("hugetlbfs", "/dev/hugepages",
                                     "hugetlbfs") is not True:
                mount_flag = "no"
                # Mount hugetlbfs on /dev/hugepages
                utils_misc.mount("hugetlbfs", "/dev/hugepages", "hugetlbfs")
            else:
                mount_flag = "yes"
            vm.start()
            session = vm.wait_for_login()
            hugepage_after_start = utils_memory.get_num_huge_pages_free()
            logging.debug("hugepage_after_start : %d" % hugepage_after_start)
            hugepage_after_start_rsvd = utils_memory.get_num_huge_pages_rsvd()
            hugepage_use = (hugepage_before_start - hugepage_after_start
                            + hugepage_after_start_rsvd)
            pid = vm.get_pid()
            anonhugepage_use = utils_memory.get_num_anon_huge_pages(pid)
            logging.debug("anonhugepage_use : %d " % anonhugepage_use)
            if compare_to_mem and hugepage_tag:
                boot_mem = vm.get_memory_size()
                range_up = hugepage_use * 2 - boot_mem
                range_down = hugepage_use * 2 - boot_mem * mem_range
                if range_up < 0:
                    error_detail = "Bootup Mem: %dMb\n\
                                    Static Hugepage Mem: %dMb\n\
                                    Transparent Hugepage Mem: %dKb"\
                                    % (boot_mem, hugepage_use, anonhugepage_use)
                    raise error.TestFail("Wrong Memory use!\n" + error_detail)

            if hugepage_num > 0 and hugepage_tag:
                if thp_enable and anonhugepage_use > 0 and hugepage_use > 0:
                    raise error.TestFail("The VM use the static hugepage"
                                         " and the transparent hugepage"
                                         " at the same time")
                elif thp_enable and anonhugepage_use > 0 and hugepage_use == 0:
                    raise error.TestFail("The VM only use the tarnsparent"
                                         "hugepage while the static hugepage"
                                         " is enable")
                elif anonhugepage_use > 0:
                    raise error.TestFail("The VM use the transparent hugepage"
                                         "while the transparent is disable")
                elif anonhugepage_use == 0 and hugepage_use == 0:
                    raise error.TestFail("The VM don't use any kinds of "
                                         "hugepage while the satic hugepage is"
                                         " enable and the transparent hugepage"
                                         " is disable")
            if hugepage_num == 0 and hugepage_tag:
                if anonhugepage_use <= 0 and thp_enable:
                    raise error.TestFail("The VM don't use any kinds of "
                                         "hugepage while the static hugepage "
                                         "is disable and the transparent "
                                         "hugepage is enable")
            if not hugepage_tag:
                if anonhugepage_use > 0 and hugepage_use > 0:
                    raise error.TestFail("The VM use both kinds of hugepage"
                                         "while the VM without hugepage tag!")
                if anonhugepage_use <= 0 and hugepage_use > 0:
                    raise error.TestFail("The VM use static hugepage"
                                         "while the VM without hugepage tag!")
                if anonhugepage_use > 0 and hugepage_use == 0:
                    raise error.TestFail("The VM use transparent hugepage"
                                         "while the VM without hugepage tag!")
            vm.destroy()
        except virt_vm.VMError, e:
            error_msg = "Test failed in positive case.\n error: %s\n" % e
            raise error.TestFail(error_msg)
    finally:
        if hugepage_tag:
            back_xml.sync()
        utils_libvirtd.libvirtd_restart()
        if mount_flag == 'yes':
            utils_misc.mount("hugetlbfs", "/dev/hugepages", "hugetlbfs")
        else:
            utils_misc.umount("hugetlbfs", "/dev/hugepages", "hugetlbfs")
        if thp_status:
            utils_memory.set_transparent_hugepage(True)
        else:
            utils_memory.set_transparent_hugepage(False)
