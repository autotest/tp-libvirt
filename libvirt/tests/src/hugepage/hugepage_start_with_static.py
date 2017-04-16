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
    3) Clean up
    """
    mountflag = params.get("hugepages_start_hugetlbfs", 'yes')
    logging.debug("MOUNTFLAG : %s " % mountflag)
    status_error = ('yes' == params.get("status_error", 'no'))
    nr_hugepage_num = int(params.get("hugepages_start_nr_hugepage_num", 0))
    thp_status = params.get("transparent_hugepage_enable", 'yes')
    hugepage_tag = params.get("hugepage_tag", 'yes')

    vm_name = params.get("main_vm")
    backup_xml = VMXML.new_from_inactive_dumpxml(vm_name)
    try:
        # Start VM to check the VM is able to be started
        try:
            if thp_status == 'yes':
                thp_flag = True
            else:
                thp_flag = False
            thp_status = utils_memory.get_transparent_hugepage()
            utils_memory.set_transparent_hugepage(thp_flag)
            if mountflag == 'yes':
                # Check the hugetlbfs mountpoint was not mounted
                logging.debug("START TO MOUNT HUGETLBFS!!!")
                if utils_misc.is_mounted("hugetlbfs", "/dev/hugepages",
                                         "hugetlbfs") is not True:
                    mountflag = "no"
                    # Mount hugetlbfs on /dev/hugepages
                    logging.debug("NOW START TO MOUNT!!!")
                    utils_misc.mount(
                        "hugetlbfs", "/dev/hugepages", "hugetlbfs")
            else:
                # Check the hugetlbfs mountpoing was mounted
                if utils_misc.is_mounted("hugetlbfs", "/dev/hugepages",
                                         "hugetlbfs") is True:
                    mountflag = "yes"
                    # Umount hugetlbfs
                    utils_misc.umount("hugetlbfs", "/dev/hugepages",
                                      "hugetlbfs")
            # Get the original number of hugepages
            old_num_huge_pages = utils_memory.get_num_huge_pages()
            logging.debug("system num of hugepages is %d" % old_num_huge_pages)
            # Change the number of hugepages
            utils_memory.set_num_huge_pages(nr_hugepage_num)
            # Set hugepage tag for the VM
            if hugepage_tag == 'yes':
                backup_xml = utils_memory.set_hugepage_tag(vm_name)
                # Restart service of libvirt
                utils_libvirtd.libvirtd_restart()
            vm = env.get_vm(vm_name)
            vm.start()
            if status_error:
                raise error.TestFail("Test successed in negative case.")
        except virt_vm.VMError, e:
            if not status_error:
                error_msg = "Test failed in positive case.\n error: %s\n" % e
                raise error.TestFail(error_msg)
    finally:
        # clean up
        backup_xml.sync()
        utils_libvirtd.libvirtd_restart()
        if mountflag is "yes":
            utils_misc.mount("hugetlbfs", "/dev/hugepages", "hugetlbfs")
        else:
            utils_misc.umount("hugetlbfs", "/dev/hugepages", "hugetlbfs")
        utils_memory.set_num_huge_pages(old_num_huge_pages)
        utils_memory.set_transparent_hugepage(thp_status)
