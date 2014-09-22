import os
import logging
import time
from autotest.client.shared import error
from virttest import virt_vm
from virttest.libvirt_xml.vm_xml import VMXML
from virttest import utils_libvirtd, utils_misc, utils_test
from virttest.staging import utils_memory


def run(test, params, env):
    """
    Test steps:

    1) Get the params from params.
    2) Strat the VM and check whether the VM been started successfully
    3) Make the VM have the Unixbench test and Check out the situation of memory
    usage
    4) Clean up
    """
    mount_flag = "yes"
    hugepage_num = int(params.get("hugepages_start_nr_hugepage_num", 0))
    thp_status = utils_memory.get_transparent_hugepage()
    thp_enable = ('yes' == params.get("transparent_hugepage_enable", 'yes'))

    hugepage_tag = ('yes' == params.get("vm_hugepage_tag", 'yes'))
    # Prepare file for unixbench
    unixbench_control_file = params.get("unixbench_controle_file",
                                        "unixbench5.control")
    params["test_control_file"] = unixbench_control_file
    vm_name = params.get("main_vm")

    vm = env.get_vm(vm_name)
    back_xml = VMXML.new_from_inactive_dumpxml(vm_name)
    # Make the vm destroy before test start
    if vm.is_alive():
        vm.destroy()
    try:
        try:
            if utils_misc.is_mounted("hugetlbfs", "/dev/hugepages",
                                     "hugetlbfs") is not True:
                mount_flag = "no"
                # Mount hugetlbfs on /dev/hugepages
                utils_misc.mount("hugetlbfs", "/dev/hugepages", "hugetlbfs")
            else:
                mount_flag = "yes"
            # Set the HugePage Number
            utils_memory.set_num_huge_pages(hugepage_num)
            # Set the transparent hugepage enable or disable
            utils_memory.set_transparent_hugepage(thp_enable)
            # Set the VM with hugepage tag
            if hugepage_tag:
                back_xml = utils_memory.set_hugepage_tag(vm_name)
                utils_libvirtd.libvirtd_restart()
            else:
                back_xml = VMXML.new_from_inactive_dumpxml(vm_name)
            # Get the hugepage number
            hugepage_before_start = utils_memory.get_num_huge_pages()
            logging.debug("hugepage_before_start : %d" % hugepage_before_start)
            vm.start()
            session = vm.wait_for_login()
            control_path = os.path.join(test.virtdir, "control",
                                        unixbench_control_file)
            command = utils_test.run_autotest(vm, session, control_path,
                                              None, None,
                                              params, copy_only=True)
            session.cmd("%s &" % command)
            time.sleep(10)

            def _is_unixbench_running():
                return (not session.cmd_status("ps -ef|grep perl|grep Run"))
            if not utils_misc.wait_for(_is_unixbench_running, timeout=200):
                raise error.TestNAError("Failed to run unixbench in guest.\n"
                                        "Since we need to run a autotest of"
                                        " unixbench in guest, so please make"
                                        " sure there are some necessary "
                                        "packages in guest, such as gcc, "
                                        "tar, bzip2")
            # Get the vm pid
            pid = vm.get_pid()
            # Get the hugepage free number from /proc/meminfo
            hugepage_after_start = utils_memory.get_num_huge_pages_free()
            # Get the hugepage reserved from /proc/meminfo
            hugepage_after_start_rsvd = utils_memory.get_num_huge_pages_rsvd()
            # Calculat the hugepage usage : Total - free + reserved
            hugepage_use = (hugepage_before_start - hugepage_after_start +
                            hugepage_after_start_rsvd)
            # Get the thp usage form /proc/pid/smaps
            anonhugepage_use = utils_memory.get_num_anon_huge_pages(pid)
            logging.debug("anonhugepage_use : %d" % anonhugepage_use)
            if hugepage_num > 0 and hugepage_tag:
                if thp_enable and anonhugepage_use > 0 and hugepage_use > 0:
                    raise error.TestFail("The VM use the static hugepage"
                                         "and the transparent hugepage"
                                         "at the same time")
                elif thp_enable and anonhugepage_use > 0 and hugepage_use == 0:
                    raise error.TestFail("The VM only use the tarnsparent"
                                         "hugepage while the static hugepage "
                                         "is enable")
                elif anonhugepage_use > 0:
                    raise error.TestFail("The VM use the transparent hugepage"
                                         "while the transparent is disable")
                elif anonhugepage_use == 0 and hugepage_use == 0:
                    raise error.TestFail("The VM don't use any kinds of "
                                         "hugepage while the static hugepage "
                                         "is enable and the transparent "
                                         "hugepage is disable")
            if hugepage_num == 0 and hugepage_tag:
                if anonhugepage_use <= 0:
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
        # clean up
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
