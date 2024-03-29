import logging
import re

from virttest import utils_test


def test_list_with_mount(test, vm, params):
    """
    1) Fall into guestfish session w/o inspector
    2) Do some necessary check
    3) Try to mount root filesystem to /
    """
    params['libvirt_domain'] = vm.name
    params['gf_inspector'] = False
    gf = utils_test.libguestfs.GuestfishTools(params)

    # Launch
    run_result = gf.run()
    if run_result.exit_status:
        gf.close_session()
        test.fail("Can not launch:%s" % run_result)
    logging.info("Launch successfully.")

    # List filesystems
    list_fs_result = gf.list_filesystems()
    if list_fs_result.exit_status:
        gf.close_session()
        test.fail("List filesystems failed:%s" % list_fs_result)
    logging.info("List filesystems successfully.")

    # List partitions
    list_part_result = gf.list_partitions()
    if list_part_result.exit_status:
        gf.close_session()
        test.fail("List partitions failed:%s" % list_part_result)
    logging.info("List partitions successfully.")

    # List devices
    list_dev_result = gf.list_devices()
    if list_dev_result.exit_status:
        gf.close_session()
        test.fail("List devices failed:%s" % list_dev_result)
    logging.info("List devices successfully.")

    # Mount root filesystem
    roots, rooto = gf.get_root()
    if roots is False:
        gf.close_session()
        test.fail("Can not get root filesystem in guestfish.")
    mount_result = gf.mount(rooto.strip(), "/")
    if mount_result.exit_status:
        gf.close_session()
        test.fail("Mount filesystem failed:%s" % mount_result)
    logging.debug("Mount filesystem successfully.")

    # List mounts
    list_df_result = gf.df()
    if list_df_result.exit_status:
        gf.close_session()
        test.fail("Df failed:%s" % list_df_result)
    logging.info("Df successfully.")


def test_list_without_mount(test, vm, params):
    """
    1) Fall into guestfish session w/o inspector
    2) Do some necessary check
    3) Try to list umounted partitions
    """
    params['libvirt_domain'] = vm.name
    params['gf_inspector'] = False
    gf = utils_test.libguestfs.GuestfishTools(params)

    # Launch
    run_result = gf.run()
    if run_result.exit_status:
        gf.close_session()
        test.fail("Can not launch:%s" % run_result)
    logging.info("Launch successfully.")

    # List filesystems
    list_fs_result = gf.list_filesystems()
    if list_fs_result.exit_status:
        gf.close_session()
        test.fail("List filesystems failed:%s" % list_fs_result)
    logging.info("List filesystems successfully.")

    # List partitions
    list_part_result = gf.list_partitions()
    if list_part_result.exit_status:
        gf.close_session()
        test.fail("List partitions failed:%s" % list_part_result)
    logging.info("List partitions successfully.")

    # List devices
    list_dev_result = gf.list_devices()
    if list_dev_result.exit_status:
        gf.close_session()
        test.fail("List devices failed:%s" % list_dev_result)
    logging.info("List devices successfully.")

    # List mounts
    list_df_result = gf.df()
    gf.close_session()
    logging.debug(list_df_result)
    if list_df_result.exit_status == 0:
        test.fail("Df successfully unexpected.")
    else:
        if not re.search("call.*mount.*first", list_df_result.stdout):
            test.fail("Unknown error.")
    logging.info("Df failed as expected.")

    logging.info("Test end as expected.")


def test_list_without_launch(test, vm, params):
    """
    1) Fall into guestfish session w/o inspector
    2) Do some necessary check w/o launch
    3) Try to mount root filesystem to /
    """
    # Get root filesystem before test
    params['libvirt_domain'] = vm.name
    params['gf_inspector'] = True
    gf = utils_test.libguestfs.GuestfishTools(params)
    roots, rootfs = gf.get_root()
    gf.close_session()
    if roots is False:
        test.error("Can not get root filesystem "
                   "in guestfish before test")

    params['gf_inspector'] = False
    gf = utils_test.libguestfs.GuestfishTools(params)

    # Do not launch

    # List filesystems
    list_fs_result = gf.list_filesystems()
    logging.debug(list_fs_result)
    if list_fs_result.exit_status == 0:
        gf.close_session()
        test.fail("List filesystems successfully")
    else:
        if not re.search("call\slaunch\sbefore", list_fs_result.stdout):
            gf.close_session()
            test.fail("Unknown error.")

    # List partitions
    list_part_result = gf.list_partitions()
    logging.debug(list_part_result)
    if list_part_result.exit_status == 0:
        gf.close_session()
        test.fail("List partitions successfully")
    else:
        if not re.search("call\slaunch\sbefore", list_part_result.stdout):
            gf.close_session()
            test.fail("Unknown error.")

    # List devices
    list_dev_result = gf.list_devices()
    logging.debug(list_dev_result)
    if list_dev_result.exit_status == 0:
        gf.close_session()
        test.fail("List devices successfully")
    else:
        if not re.search("call\slaunch\sbefore", list_dev_result.stdout):
            gf.close_session()
            test.fail("Unknown error.")

    # Mount root filesystem
    mount_result = gf.mount(rootfs, "/")
    logging.debug(mount_result)
    gf.close_session()
    if mount_result.exit_status == 0:
        test.fail("Mount filesystem successfully")
    else:
        if not re.search("call\slaunch\sbefore", mount_result.stdout):
            test.fail("Unknown error.")

    logging.info("Test end as expected.")


def test_list_with_inspector(test, vm, params):
    """
    1) Fall into guestfish session w/ mounting root filesystem
    2) Do some necessary check
    """
    # Get root filesystem before test
    params['libvirt_domain'] = vm.name
    params['gf_inspector'] = True
    gf = utils_test.libguestfs.GuestfishTools(params)
    roots, rootfs = gf.get_root()
    gf.close_session()
    if roots is False:
        test.error("Can not get root filesystem "
                   "in guestfish before test")

    params['gf_inspector'] = False
    params['mount_options'] = "%s:/" % rootfs
    gf = utils_test.libguestfs.GuestfishTools(params)

    # List filesystems
    list_fs_result = gf.list_filesystems()
    logging.debug(list_fs_result)
    if list_fs_result.exit_status:
        gf.close_session()
        test.fail("List filesystems failed")
    logging.info("List filesystems successfully.")

    # List partitions
    list_part_result = gf.list_partitions()
    logging.debug(list_part_result)
    if list_part_result.exit_status:
        gf.close_session()
        test.fail("List partitions failed")
    logging.info("List partitions successfully.")

    # List devices
    list_dev_result = gf.list_devices()
    logging.debug(list_dev_result)
    if list_dev_result.exit_status:
        gf.close_session()
        test.fail("List devices failed")
    logging.info("List devices successfully.")

    # List mounts
    list_df_result = gf.df()
    gf.close_session()
    logging.debug(list_df_result)
    if list_df_result.exit_status:
        test.fail("Df failed")
    logging.info("Df successfully.")


def run(test, params, env):
    """
    Test guestfs with list commands: list-partitions, list-filesystems
                                     list-devices
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    # guestfish cannot operate living vm
    if vm.is_alive():
        vm.destroy()

    operation = params.get("list_operation")
    testcase = globals()["test_%s" % operation]
    testcase(test, vm, params)
