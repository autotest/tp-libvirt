import logging
import os
from autotest.client.shared import error
from virttest import virt_vm, data_dir
from virttest.libvirt_xml.vm_xml import VMXML
from virttest import utils_libvirtd, utils_misc
from virttest.staging import utils_memory
from virttest import remote


def run(test, params, env):
    """
    Test steps:

    1) Get the params from params.
    2) Strat the VM and check whether the VM been started successfully
    3) Copy a c file to the VM.Compile it.Run the exe.
    4) Check the hugepage usage.
    3) Clean up
    """
    hugepage_num = int(params.get("hugepages_start_nr_hugepage_num", 0))
    thp_status = utils_memory.get_transparent_hugepage()
    thp_enable = ('yes' == params.get("transparent_hugepage_enable", 'yes'))
    hugepage_tag = ('yes' == params.get("vm_hugepage_tag", 'yes'))
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    back_xml = VMXML.new_from_inactive_dumpxml(vm_name)
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
            # GET a C file
            #stress_path = utils_hugepage.prepare_c_file()
            shared_dir = os.path.dirname(data_dir.get_data_dir())
            stress_path = os.path.join(shared_dir, "scripts", "hugepage_mem_test.c")
            vm.start()
            logging.debug("vm START successfull!!!")
            session = vm.wait_for_login()
            vm_addr = vm.get_address()
            logging.debug("vm_addr : %s" % vm_addr)
            password = params.get('password')
            target_path = params.get("target_path", "/tmp/test.out")
            vm_stress_path = "/tmp/"
            remote.scp_to_remote(vm_addr, 22, 'root', password, stress_path,
                                 vm_stress_path)
            session.cmd("gcc %s -o %s" % (stress_path, target_path))
            session.cmd("%s &" % target_path)
            pid = vm.get_pid()
            anonhugepage_use = utils_memory.get_num_anon_huge_pages(pid)
            logging.debug("AnonHugePage_use : %d" % anonhugepage_use)
            hugepage_after_start = utils_memory.get_num_huge_pages_free()
            hugepage_after_start_rsvd = utils_memory.get_num_huge_pages_rsvd()
            hugepage_use = (hugepage_before_start - hugepage_after_start
                            - hugepage_after_start_rsvd)
            logging.debug("Hugepage_use : %d " % hugepage_use)
            if hugepage_num > 0 and hugepage_tag:
                if thp_enable and anonhugepage_use > 0 and hugepage_use > 0:
                    raise error.TestFail("The VM use the static hugepage"
                                         "and the transparent hugepage"
                                         "at the same time")
                elif thp_enable and anonhugepage_use > 0 and hugepage_use == 0:
                    raise error.TestFail("The VM only use the tarnsparent "
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
