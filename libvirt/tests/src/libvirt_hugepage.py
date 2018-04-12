import logging
import os
import time

from aexpect import ShellError

from avocado.utils import process

from virttest import virsh
from virttest import utils_libvirtd
from virttest import remote
from virttest import utils_misc
from virttest import utils_test
from virttest import utils_package
from virttest.remote import LoginError
from virttest.virt_vm import VMError
from virttest.libvirt_xml import vm_xml
from virttest.staging import utils_memory


def prepare_c_file():
    outputlines = """
    #include<stdio.h>
    #include<malloc.h>
    #include<string.h>
    #include<unistd.h>
    #define MAX 1024*1024*512
    int main(){
        int *a;
        a=(int*)malloc(MAX);
        int *b;
        b=(int*)malloc(MAX);
        while(1){
            memcpy(b,a,MAX);
            memcpy(a,b,MAX);
            sleep(2);
            }
        return 0;
    }
    """
    with open("/tmp/test.c", 'w') as output:
        output.writelines(outputlines)
    return "/tmp/test.c"


def run(test, params, env):
    """
    Test steps:

    1) Get the params from params.
    2) check the environment
    3) Strat the VM and check whether the VM been started successfully
    4) Compare the Hugepage memory size to the Guest memory setted.
    5) Check the hugepage memory usage.
    6) Clean up
    """
    test_type = params.get("test_type", 'normal')
    tlbfs_enable = 'yes' == params.get("hugetlbfs_enable", 'no')
    shp_num = int(params.get("static_hugepage_num", 1024))
    thp_enable = 'yes' == params.get("trans_hugepage_enable", 'no')
    mb_enable = 'yes' == params.get("mb_enable", 'yes')
    delay = int(params.get("delay_time", 10))

    # Skip cases early
    vm_names = []
    if test_type == "contrast":
        vm_names = params.get("vms").split()[:2]
        if len(vm_names) < 2:
            test.cancel("This test requires two VMs")
        # confirm no VM running
        allvms = virsh.dom_list('--name').stdout.strip()
        if allvms != '':
            test.cancel("one or more VMs are alive")
        err_range = float(params.get("mem_error_range", 1.25))
    else:
        vm_names.append(params.get("main_vm"))
        if test_type == "stress":
            target_path = params.get("target_path", "/tmp/test.out")
        elif test_type == "unixbench":
            unixbench_control_file = params.get("unixbench_controle_file",
                                                "unixbench5.control")

    # backup orignal setting
    shp_orig_num = utils_memory.get_num_huge_pages()
    thp_orig_status = utils_memory.get_transparent_hugepage()
    page_size = utils_memory.get_huge_page_size()

    # mount/umount hugetlbfs
    tlbfs_status = utils_misc.is_mounted("hugetlbfs", "/dev/hugepages",
                                         "hugetlbfs")
    if tlbfs_enable is True:
        if tlbfs_status is not True:
            utils_misc.mount("hugetlbfs", "/dev/hugepages", "hugetlbfs")
    else:
        if tlbfs_status is True:
            utils_misc.umount("hugetlbfs", "/dev/hugepages", "hugetlbfs")

    # set static hugepage
    utils_memory.set_num_huge_pages(shp_num)

    # enable/disable transparent hugepage
    if thp_enable:
        utils_memory.set_transparent_hugepage('always')
    else:
        utils_memory.set_transparent_hugepage('never')

    # set/del memoryBacking tag
    for vm_name in vm_names:
        if mb_enable:
            vm_xml.VMXML.set_memoryBacking_tag(vm_name)
        else:
            vm_xml.VMXML.del_memoryBacking_tag(vm_name)

    utils_libvirtd.libvirtd_restart()
    non_started_free = utils_memory.get_num_huge_pages_free()

    vms = []
    sessions = []
    try:
        for vm_name in vm_names:
            # try to start vm and login
            try:
                vm = env.get_vm(vm_name)
                vm.start()
            except VMError as e:
                if mb_enable and not tlbfs_enable:
                    # if hugetlbfs not be mounted,
                    # VM start with memoryBacking tag will fail
                    logging.debug(e)
                else:
                    error_msg = "Test failed in positive case. error: %s\n" % e
                    test.fail(error_msg)
            if vm.is_alive() is not True:
                break
            vms.append(vm)

            # try to login and run some program
            try:
                session = vm.wait_for_login()
            except (LoginError, ShellError) as e:
                error_msg = "Test failed in positive case.\n error: %s\n" % e
                test.fail(error_msg)
            sessions.append(session)

            if test_type == "stress":
                # prepare file for increasing stress
                stress_path = prepare_c_file()
                remote.scp_to_remote(vm.get_address(), 22,
                                     'root', params.get('password'),
                                     stress_path, "/tmp/")
                # Try to install gcc on guest first
                utils_package.package_install(["gcc"], session, 360)
                # increasing workload
                session.cmd("gcc %s -o %s" % (stress_path, target_path))
                session.cmd("%s &" % target_path)

            if test_type == "unixbench":
                params["main_vm"] = vm_name
                params["test_control_file"] = unixbench_control_file

                control_path = os.path.join(test.virtdir, "control",
                                            unixbench_control_file)
                # unixbench test need 'patch' and 'perl' commands installed
                utils_package.package_install(["patch", "perl"], session, 360)
                command = utils_test.run_autotest(vm, session, control_path,
                                                  None, None, params,
                                                  copy_only=True)
                session.cmd("%s &" % command, ignore_all_errors=True)
                # wait for autotest running on vm
                time.sleep(delay)

                def _is_unixbench_running():
                    cmd = "ps -ef | grep perl | grep Run"
                    return not session.cmd_status(cmd)
                if not utils_misc.wait_for(_is_unixbench_running, timeout=240):
                    test.cancel("Failed to run unixbench in guest,"
                                " please make sure some necessary"
                                " packages are installed in guest,"
                                " such as gcc, tar, bzip2")
                logging.debug("Unixbench test is running in VM")

        if test_type == "contrast":
            # wait for vm finish starting completely
            time.sleep(delay)

        if not (mb_enable and not tlbfs_enable):
            logging.debug("starting analyzing the hugepage usage...")
            pid = vms[-1].get_pid()
            started_free = utils_memory.get_num_huge_pages_free()
            # Get the thp usage from /proc/pid/smaps
            started_anon = utils_memory.get_num_anon_huge_pages(pid)
            static_used = non_started_free - started_free
            hugepage_used = static_used * page_size

            if test_type == "contrast":
                # get qemu-kvm memory consumption by top
                cmd = "top -b -n 1|awk '$1 == %s {print $10}'" % pid
                rate = process.run(cmd, ignore_status=False,
                                   verbose=True, shell=True).stdout_text.strip()
                qemu_kvm_used = (utils_memory.memtotal() * float(rate)) / 100
                logging.debug("rate: %s, used-by-qemu-kvm: %f, used-by-vm: %d",
                              rate, qemu_kvm_used, hugepage_used)
                if abs(qemu_kvm_used - hugepage_used) > hugepage_used * (err_range - 1):
                    test.fail("Error for hugepage usage")
            if test_type == "stress":
                if non_started_free <= started_free:
                    logging.debug("hugepage usage:%d -> %d", non_started_free,
                                  started_free)
                    test.fail("Error for hugepage usage with stress")
            if mb_enable is not True:
                if static_used > 0:
                    test.fail("VM use static hugepage without"
                              " memoryBacking element")
                if thp_enable is not True and started_anon > 0:
                    test.fail("VM use transparent hugepage, while"
                              " it's disabled")
            else:
                if tlbfs_enable is not True:
                    if static_used > 0:
                        test.fail("VM use static hugepage without tlbfs"
                                  " mounted")
                    if thp_enable and started_anon <= 0:
                        test.fail("VM doesn't use transparent"
                                  " hugepage")
                else:
                    if shp_num > 0:
                        if static_used <= 0:
                            test.fail("VM doesn't use static"
                                      " hugepage")
                    else:
                        if static_used > 0:
                            test.fail("VM use static hugepage,"
                                      " while it's set to zero")
                    if thp_enable is not True:
                        if started_anon > 0:
                            test.fail("VM use transparent hugepage,"
                                      " while it's disabled")
                    else:
                        if shp_num == 0 and started_anon <= 0:
                            test.fail("VM doesn't use transparent"
                                      " hugepage, while static"
                                      " hugepage is disabled")
    finally:
        # end up session
        for session in sessions:
            session.close()

        for vm in vms:
            if vm.is_alive():
                vm.destroy()

        for vm_name in vm_names:
            if mb_enable:
                vm_xml.VMXML.del_memoryBacking_tag(vm_name)
            else:
                vm_xml.VMXML.set_memoryBacking_tag(vm_name)

        utils_libvirtd.libvirtd_restart()

        if tlbfs_enable is True:
            if tlbfs_status is not True:
                utils_misc.umount("hugetlbfs", "/dev/hugepages", "hugetlbfs")
        else:
            if tlbfs_status is True:
                utils_misc.mount("hugetlbfs", "/dev/hugepages", "hugetlbfs")
        utils_memory.set_num_huge_pages(shp_orig_num)
        utils_memory.set_transparent_hugepage(thp_orig_status)
