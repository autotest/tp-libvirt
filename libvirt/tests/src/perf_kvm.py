import os
import re
import shutil

import aexpect

from avocado.utils import process
from avocado.utils import path

from virttest import data_dir
from virttest import ssh_key


def _perf_kvm_help():
    """
    Execute the perf kvm --help to verify perf kvm is available
    on this host.
    """
    result = process.run("perf kvm --help", ignore_status=True, shell=True)
    if result.exit_status:
        return None
    return "perf kvm"


def find_first_kernel_symbol(filename, symbol_type):
    """
    Find the first symbol(max percent) of in the result file.

    Note: if the file is result of perf top, there are lots
    of "^[[H^[[2J" to clean screen in the file, then we have to
    truncate the file for the last content of perf top.
    """
    with open(filename) as result_file:
        lines = result_file.readlines()
    trunc_start = 0
    trunc_end = 0
    for index in range(len(lines)):
        line = lines[index]
        if line.count("\x1b[H\x1b[2J"):
            trunc_start = trunc_end
            trunc_end = index
    if not trunc_start == trunc_end:
        lines = lines[trunc_start:trunc_end]
    for line in lines:
        sub_lines = line.split()
        if not sub_lines:
            continue
        if re.match(r"[\d|\.]*%", sub_lines[0]):
            print(line)
            if not (sub_lines[-2] == ("[%s]" % symbol_type)):
                continue
            else:
                return sub_lines[-1]


def find_symbol_in_result(filename, symbol_name):
    """
    Find symbol in file, return the index of symbol.
    """
    with open(filename) as result_file:
        lines = result_file.readlines()
    trunc_start = 0
    trunc_end = 0
    for index in range(len(lines)):
        line = lines[index]
        if line.count("\x1b[H\x1b[2J"):
            trunc_start = trunc_end
            trunc_end = index
    if not trunc_start == trunc_end:
        lines = lines[trunc_start:trunc_end]
    result = 0
    for index in range(len(lines)):
        line = lines[index]
        sub_lines = line.split()
        if not sub_lines:
            continue
        if re.match(r"[\d|\.]*%", sub_lines[0]):
            if not (sub_lines[-1] == (symbol_name)):
                print(line)
                result = result + 1
                continue
            else:
                return result
    return -1


def mount_guestfs_with_sshfs(test, vms):
    """
    Mount the guest filesystem with sshfs.
    """
    guestmount_path = os.path.join(data_dir.get_tmp_dir(), "guestmount")
    if not (os.path.isdir(guestmount_path)):
        os.makedirs(guestmount_path)
    sshfs_cmd = "sshfs -o allow_other,direct_io "
    for vm in vms:
        specific_path = os.path.join(guestmount_path, str(vm.get_pid()))
        if not os.path.isdir(specific_path):
            os.makedirs(specific_path)
        ssh_key.setup_ssh_key(hostname=vm.get_address(),
                              user=vm.params.get("username", ""),
                              password=vm.params.get("password", ""),
                              port=22)
        cmd = "%s %s:/ %s" % (sshfs_cmd, vm.get_address(), specific_path)
        result = process.run(cmd, ignore_status=True, shell=True)
        if result.exit_status:
            test.fail("Failed to use sshfs for guestmount.\n"
                      "Detail:%s." % result)
    return guestmount_path


def umount_guestfs_with_sshfs(vms):
    """
    Umount the guest filesystem we mounted in test.
    """
    guestmount_path = os.path.join(data_dir.get_tmp_dir(), "guestmount")
    for vm in vms:
        specific_path = os.path.join(guestmount_path, str(vm.get_pid()))
        if (os.path.isdir(specific_path)):
            cmd = "umount %s" % specific_path
            process.run(cmd, ignore_status=True, shell=True)
            shutil.rmtree(specific_path)


def get_kernel_file(vm):
    """
    Get the kernel info file from guest.
    """
    guestkallsyms = os.path.join(data_dir.get_tmp_dir(), "kallsyms")
    guestmodules = os.path.join(data_dir.get_tmp_dir(), "modules")
    # scp will miss the content of /proc/kallsysm.
    session = vm.wait_for_login()
    session.cmd("cat /proc/kallsyms > /root/kallsyms")
    session.cmd("cat /proc/modules > /root/modules")
    vm.copy_files_from("/root/kallsyms", guestkallsyms)
    vm.copy_files_from("/root/modules", guestmodules)

    return (guestkallsyms, guestmodules)


def run(test, params, env):
    """
    Test for perf kvm command.

    1) Check the perf kvm on host.
    2) Get variables.
    3) generate perf kvm command.
    4) Mount guest filesystem for --guestmount
        or get kernel info files from guest for --guestkallsyms
    5) Execute command for each case.
    6) Verify the result, compare the content from host and guest.
    7) Cleanup.
    """
    perf_kvm_exec = _perf_kvm_help()
    if not perf_kvm_exec:
        test.cancel("No perf-kvm found in your host.")
    vm = env.get_vm(params.get("main_vm", "avocado-vt-vm1"))
    vms = env.get_all_vms()
    guestmount = ("yes" == params.get("perf_kvm_guestmount", "no"))

    host = ("yes" == params.get("perf_kvm_host", "no"))
    guest = ("yes" == params.get("perf_kvm_guest", "no"))

    multi_guest = ("yes" == params.get("perf_kvm_multi_guest", "no"))

    top = ("yes" == params.get("perf_kvm_top", "no"))
    record = ("yes" == params.get("perf_kvm_record", "no"))
    report = ("yes" == params.get("perf_kvm_report", "no"))
    diff = ("yes" == params.get("perf_kvm_diff", "no"))
    buildid_list = ("yes" == params.get("perf_kvm_buildid_list", "no"))

    guestmount_path = None
    guestkallsyms_path = None
    guestmodules_path = None

    output_of_record = os.path.join(data_dir.get_tmp_dir(), "record.output")
    # As diff command need two files, init a variable for it.
    output_for_diff = os.path.join(data_dir.get_tmp_dir(), "record.output.diff")
    host_result_file = os.path.join(data_dir.get_tmp_dir(), "perf_kvm_result")
    guest_result_file = os.path.join(data_dir.get_tmp_dir(), "guest_result")
    result_on_guest = "/root/result"

    command = perf_kvm_exec

    if host:
        command = "%s --host" % command
    if guest:
        command = "%s --guest" % command

    session = vm.wait_for_login()
    try:

        if guestmount:
            try:
                path.find_command("sshfs")
            except path.CmdNotFoundError:
                test.cancel("Please install fuse-sshfs for perf kvm "
                            "with --guestmount.")
            if multi_guest:
                if len(vms) < 2:
                    test.cancel("Only one vm here, skipping "
                                "this case for multi-guest.")
            guestmount_path = mount_guestfs_with_sshfs(test, vms)
            command = "%s --guestmount %s" % (command, guestmount_path)
        else:
            guestkallsyms_path, guestmodules_path = get_kernel_file(vm)
            command = "%s --guestkallsyms %s --guestmodules %s" % (command,
                                                                   guestkallsyms_path,
                                                                   guestmodules_path)

        session.cmd("dd if=/dev/zero of=/dev/null bs=1 count=1G &")

        if top:
            session = vm.wait_for_login()
            # Time for top, there is no sleep subcommand in perf top such as
            # in perf record, then we can not control the time in perf command.
            # So, we have to use timeout command to wrap it here.
            host_command = "timeout 30 %s top 1>%s" % (command, host_result_file)
            guest_command = "timeout 30 perf top >%s" % (result_on_guest)
            host_session = aexpect.ShellSession("sh")
            host_session.sendline(host_command)
            _, output = session.cmd_status_output(guest_command)
            host_session.close()

            if (host and guest):
                vm.copy_files_from(result_on_guest, guest_result_file)
                host_first = find_first_kernel_symbol(host_result_file, "g")
                index_in_guest = find_symbol_in_result(guest_result_file, host_first)
                if index_in_guest < 0:
                    test.fail("Not find symbol %s in guest result." % host_first)
                if index_in_guest > 5:
                    test.fail("Perf information for guest is not correct."
                              "The first symbol in host_result is %s, "
                              "but this symbol is in %s index in result "
                              "from guest.\n" % (host_first, index_in_guest))
        if record:
            session = vm.wait_for_login()
            host_command = "%s record -a sleep 10 " % (command)
            guest_command = "perf record -a sleep 10 &"
            status, output = session.cmd_status_output(guest_command)
            if status:
                test.cancel("Please make sure there is perf command "
                            "on guest.\n Detail: %s." % output)
            result = process.run(host_command, ignore_status=True, shell=True)
            if result.exit_status:
                test.fail(result)

        if report:
            session = vm.wait_for_login()
            host_command = "%s report 1>%s" % (command, host_result_file)
            guest_command = "perf report 1>%s" % (result_on_guest)
            status, output = session.cmd_status_output(guest_command)
            if status:
                test.cancel("Please make sure there is perf command "
                            "on guest.\n Detail: %s." % output)
            result = process.run(host_command, ignore_status=True, shell=True)
            if result.exit_status:
                test.fail(result)

            if (host and guest):
                vm.copy_files_from(result_on_guest, guest_result_file)
                host_first = find_first_kernel_symbol(host_result_file, "g")
                index_in_guest = find_symbol_in_result(guest_result_file, host_first)
                if index_in_guest < 0:
                    test.fail("Not find symbol %s in guest result." % host_first)
                if index_in_guest > 5:
                    test.fail("Perf information for guest is not correct."
                              "The first symbol in host_result is %s, "
                              "but this symbol is in %s index in result "
                              "from guest.\n" % (host_first, index_in_guest))
        if diff:
            session = vm.wait_for_login()
            host_command = "%s record -o %s -a sleep 10" % (command, output_of_record)
            # Run twice to capture two perf data files for diff.
            result = process.run(host_command, ignore_status=True, shell=True)
            if result.exit_status:
                test.fail(result)
            host_command = "%s record -o %s -a sleep 10" % (command, output_for_diff)
            result = process.run(host_command, ignore_status=True, shell=True)
            if result.exit_status:
                test.fail(result)
            host_command = "%s diff %s %s" % (command, output_of_record, output_for_diff)
            result = process.run(host_command, ignore_status=True, shell=True)
            if result.exit_status:
                test.fail(result)

        if buildid_list:
            host_command = "%s buildid-list" % command
            result = process.run(host_command, ignore_status=True, shell=True)
            if result.exit_status:
                test.fail(result)
    finally:
        if session:
            session.close()

        umount_guestfs_with_sshfs(vms)
        if guestkallsyms_path and os.path.exists(guestkallsyms_path):
            os.remove(guestkallsyms_path)
        if guestmodules_path and os.path.exists(guestmodules_path):
            os.remove(guestmodules_path)
        if host_result_file and os.path.exists(host_result_file):
            os.remove(host_result_file)
        if guest_result_file and os.path.exists(guest_result_file):
            os.remove(guest_result_file)
