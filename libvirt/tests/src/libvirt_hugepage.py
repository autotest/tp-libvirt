import logging
import os
import time

from autotest.client import utils
from autotest.client.shared import error

from virttest import virsh, utils_libvirtd, remote, utils_misc, utils_test
from virttest.remote import LoginError
from virttest.virt_vm import VMError
from virttest.aexpect import ShellError
from virttest.libvirt_xml import vm_xml
from virttest.staging import utils_memory


def prepare_c_file():
    output = file("/tmp/test.c", 'w')
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
    output.writelines(outputlines)
    output.close()
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

    # backup orignal setting
    shp_orig_num = utils_memory.get_num_huge_pages()
    thp_orig_status = utils_memory.get_transparent_hugepage()
    page_size = utils_memory.get_huge_page_size()

    if test_type == "contrast":
        range = float(params.get("mem_error_range", 1.25))
    elif test_type == "stress":
        target_path = params.get("target_path", "/tmp/test.out")
    elif test_type == "unixbench":
        unixbench_control_file = params.get("unixbench_controle_file",
                                            "unixbench5.control")

    vm_names = []
    if test_type == "contrast":
        vm_names = params.get("vms").split()[:2]
        if len(vm_names) < 2:
            raise error.TestNAError("Hugepage Stress Test need two VM(s).")
        # confirm no VM(s) running
        allvms = virsh.dom_list('--name').stdout.strip()
        if allvms != '':
            raise error.TestNAError("one or more VM(s) is living.")
    else:
        vm_names.append(params.get("main_vm"))

    # mount/umount hugetlbfs
    tlbfs_status = utils_misc.is_mounted("hugetlbfs", "/dev/hugepages", "hugetlbfs")
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
    for vm_name in vm_names:
        # try to start vm and login
        try:
            vm = env.get_vm(vm_name)
            vm.start()
        except VMError, e:
            if mb_enable and not tlbfs_enable:
                # if hugetlbfs not be mounted,
                # VM start with memoryBacking tag will fail
                logging.debug(e)
                pass  # jump out of for-loop
            else:
                error_msg = "Test failed in positive case. error: %s\n" % e
                raise error.TestFail(error_msg)
        if vm.is_alive() is not True:
            break
        vms.append(vm)

        # try to login and run some program
        try:
            session = vm.wait_for_login()
        except (LoginError, ShellError), e:
            error_msg = "Test failed in positive case.\n error: %s\n" % e
            raise error.TestFail(error_msg)
        sessions.append(session)

        if test_type == "stress":
            # prepare file for increasing stress
            stress_path = prepare_c_file()
            remote.scp_to_remote(vm.get_address(), 22, 'root',
                                 params.get('password'), stress_path, "/tmp/")
            # increasing workload
            session.cmd("gcc %s -o %s" % (stress_path, target_path))
            session.cmd("%s &" % target_path)

        if test_type == "unixbench":
            params["main_vm"] = vm_name
            params["test_control_file"] = unixbench_control_file

            control_path = os.path.join(test.virtdir, "control",
                                        unixbench_control_file)
            command = utils_test.run_autotest(vm, session, control_path,
                                              None, None,
                                              params, copy_only=True)
            session.cmd("%s &" % command)
            # wait for autotest running on vm
            time.sleep(delay)

            def _is_unixbench_running():
                return (not session.cmd_status("ps -ef | grep perl | grep Run"))
            if not utils_misc.wait_for(_is_unixbench_running, timeout=240):
                raise error.TestNAError("Failed to run unixbench in guest.\n"
                                        "so please make sure there are some necessary"
                                        "packages in guest, such as gcc, tar, bzip2")
            logging.debug("Unixbench is already running in VM(s).")

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
            cmd = "top -b -n 1|grep 'qemu'|grep '%s'|awk '{print $10}'" % pid
            rate = utils.run(cmd, ignore_status=False, verbose=True).stdout.strip()
            qemu_kvm_used = (utils_memory.memtotal() * float(rate)) / 100
            logging.debug("rate:%s, used-by-qemu-kvm: %f, used-by-vm: %d"
                          % (rate, qemu_kvm_used, hugepage_used))
            if abs(qemu_kvm_used - hugepage_used) > hugepage_used * (range - 1):
                raise error.TestFail("Error for hugepage usage")
        if test_type == "stress":
            if non_started_free <= started_free:
                logging.debug("hugepage usage:%d -> %d" % non_started_free, started_free)
                raise error.TestFail("Error for hugepage usage with stress")
        if mb_enable is not True:
            if static_used > 0:
                raise error.TestFail("VM use static hugepage without memoryBacking")
            if thp_enable is not True and started_anon > 0:
                raise error.TestFail("VM use transparent hugepage, while it's disabled")
        else:
            if tlbfs_enable is not True:
                if static_used > 0:
                    error.TestFail("VM use static hugepage, while tlbfs won't be mounted")
                if thp_enable and started_anon <= 0:
                    raise error.TestFail("VM doesn't use transparent hugepage")
            else:
                if shp_num > 0:
                    if static_used <= 0:
                        raise error.TestFail("VM doesn't use static hugepage")
                else:
                    if static_used > 0:
                        raise error.TestFail("VM use static hugepage, while it's set to zero")
                if thp_enable is not True:
                    if started_anon > 0:
                        raise error.TestFail("VM use transparent hugepage, while it's disabled")
                else:
                    if shp_num == 0 and started_anon <= 0:
                        raise error.TestFail("VM doesn't use transparent hugepage,"
                                             "while static hupage is disabled")

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
