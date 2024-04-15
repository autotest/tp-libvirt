import logging as log
import os

from virttest.utils_misc import cmd_status_output
from virttest import virsh

logging = log.getLogger('avocado.' + __name__)
cleanup = []


def get_kernel_cmdline(vm):
    """
    Returns /proc/cmdline

    :param vm: VM instance
    """
    with vm.wait_for_login() as session:
        _, o = cmd_status_output("cat /proc/cmdline",
                                 session=session,
                                 ignore_status=False,
                                 shell=True)
    return o.strip()


def decrypt(dump_file, decrypted_dump_dir, comm_key):
    """
    Decrypt the dumped file with a communication key.

    :param dump_file: path to the memory dump
    :param decrypted_dump_dir: where to mount the decrypted
                                dump
    :param comm_key: string with the communication key
    """
    cmd = "echo %s > %s" % (comm_key, "comm_key.txt")
    cmd_status_output(cmd, ignore_status=False, shell=True)
    cmd = "zgetdump -k comm_key.txt %s -m %s" % (dump_file, decrypted_dump_dir)
    cmd_status_output(cmd, ignore_status=False, shell=True)
    cleanup.append(lambda: cmd_status_output("umount %s" % decrypted_dump_dir))


def confirm_contains(decrypted_dump_file, text, test):
    """
    Confirm that given text can be found in file.
    This confirms the file has been decrypted correctly.

    :param decrypted_dump_file: The path of the .elf file
                                of the decrypted dump
    :param text: string to be found
    :param test: test instance
    """
    s, _ = cmd_status_output("strings -d %s|grep -m 1 '%s'" % (decrypted_dump_file, text),
                             shell=True)
    if s:
        test.fail("Couldn't find expected text in dump file.")


def run(test, params, env):
    """
    Decrypt encrypted memory dump.
    Assumes the guest is running in with
    SE and has been encrypted with the tested
    communication key.
    """

    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)
    comm_key = params.get("comm_key")
    dump_file = "/tmp/DUMP"
    decrypted_dump_dir = "/mnt"
    decrypted_dump_file = os.path.join(decrypted_dump_dir, "dump.elf")

    try:
        cmdline = get_kernel_cmdline(vm)
        virsh.dump(vm_name, dump_file, "--memory-only",
                   ignore_status=False, debug=True)
        cleanup.append(lambda: os.remove(dump_file))

        decrypt(dump_file, decrypted_dump_dir, comm_key)
        confirm_contains(decrypted_dump_file, cmdline, test)

    finally:
        for c in reversed(cleanup):
            c()
