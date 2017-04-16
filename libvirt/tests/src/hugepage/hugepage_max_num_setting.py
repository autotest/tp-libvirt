import logging
from autotest.client.shared import error
from virttest import utils_misc
from virttest.staging import utils_memory


def run(test, params, env):
    """
    Test steps:

    1) Get the params from params.
    2) Set the number of Hugepages and check it after setting.Compare it to the
    MemoryTotal
    3) Clean up
    """
    # mount the hugetlbfs
    if utils_misc.is_mounted("hugetlbfs", "/dev/hugepages",
                             "hugetlbfs") is not True:
        mount_flag = "no"
        # Mount hugetlbfs on /dev/hugepages
        utils_misc.mount("hugetlbfs", "/dev/hugepages", "hugetlbfs")
    else:
        mount_flag = "yes"
    # Get the total memory size from the /proc/meminfo
    mem_total = utils_memory.memtotal()
    # Calculate the max number of hugepage that can be setting
    mem_max_num = mem_total / 2048
    # Set the number of hugepage exceeded the limit of Memeory
    mem_max_set = mem_max_num + 100
    utils_memory.set_num_huge_pages(mem_max_set)
    # Get the number of hugepage after setting the max number of Hugepage
    mem_num = utils_memory.get_num_huge_pages()
    try:
        # Judge the actual number of Hugepage is not exceeded the
        # limit of memory
        if mem_max_set == mem_num:
            error_detail = ("mem_total : %d\nmem_max_num : %d\n"
                            "mem_max_set : %d\nmem_num : %d"
                            % (mem_total, mem_max_num, mem_max_set, mem_num))
            raise error.TestFail("HugePage memory setting has "
                                 "exceeded the limit of memory." + error_detail)
        # Judge the actual number of Hugepage is not much smaller than the memory
        # total can be set.
        elif mem_num - mem_max_num >= 0:
            error_detail = ("mem_total : %d\nmem_max_num : %d\n"
                            "mem_max_set : %d\nmem_num : %d"
                            % (mem_total, mem_max_num, mem_max_set, mem_num))
            raise error.TestFail("HugePage memory setting has "
                                 "exceeded the limit of memory." + error_detail)
    finally:
        # clean up
        utils_memory.set_num_huge_pages(0)
        if mount_flag == 'yes':
            utils_misc.mount("hugetlbfs", "/dev/hugepages", "hugetlbfs")
        else:
            utils_misc.umount("hugetlbfs", "/dev/hugepages", "hugetlbfs")
