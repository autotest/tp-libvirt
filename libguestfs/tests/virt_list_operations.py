import logging
import re

from virttest import utils_libguestfs as lgf
from virttest import utils_test


def run(test, params, env):
    """
    Test libguestfs with list commands: virt-list-partitions, virt-filesystems
                                        virt-df, virt-list-filesystems
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    if vm.is_alive():
        vm.destroy()

    # List filesystems with virt-list-filesystems
    list_fs_result = lgf.virt_list_filesystems(vm_name, debug=True)
    filesystems1 = list_fs_result.stdout_text.splitlines()
    if list_fs_result.exit_status:
        test.fail("List filesystems with virt-list-filesystems "
                  "failed:%s" % list_fs_result)
    logging.info("List filesystems successfully.")

    # List filesystems with virt-filesystems
    virt_fs_result = lgf.virt_filesystems(vm_name, debug=True)
    filesystems2 = virt_fs_result.stdout_text.splitlines()
    if virt_fs_result.exit_status:
        test.fail("List filesystems with virt-filesystems failed:"
                  "%s" % virt_fs_result.stdout_text.strip())
    logging.info("List filesystems successfully.")

    for fs in filesystems1:
        if fs not in filesystems2:
            test.fail("Two ways to get filesystems do not match.")

    # List partitions
    list_part_result = lgf.virt_list_partitions(vm_name, debug=True)
    partitions = list_part_result.stdout_text.splitlines()
    if list_part_result.exit_status:
        test.fail("List partitions failed:%s" % list_part_result)
    logging.info("List partitions successfully.")

    # List mounts
    list_df_result = lgf.virt_df(vm_name, debug=True)
    if list_df_result.exit_status:
        test.fail("Df failed:%s" % list_df_result)
    logging.info("Df successfully.")

    # Check partitions in vm
    if vm.is_dead():
        vm.start()
    session = vm.wait_for_login()
    try:
        for part in partitions:
            if utils_test.libguestfs.primary_disk_virtio(vm):
                part = re.sub("/dev/sda", "/dev/vda", part)
            if session.cmd_status("ls %s" % part):
                test.fail("Did not find partition %s listed with "
                          "virt-list-partitions in vm." % part)
    finally:
        session.close()
        vm.destroy()
