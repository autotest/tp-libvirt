import os
import platform

from avocado.utils import process

from virttest import libvirt_version
from virttest import remote
from virttest import utils_disk
from virttest import utils_package
from virttest import utils_test
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_secret
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.migration import base_steps
from provider.migration import migration_vtpm

#src_sec_uuid = None
#dst_sec_uuid = None
dev_src = None
dev_target = None
target_luns = None


def check_ownership(path, expected_ownership, test, remote_host=False, file_list=[]):
    """
    Check ownership

    :param swtpm_log: swtpm log path
    :param expected_ownership: Expected ownership
    :param test: test object
    """
    cmd = "ls -alZ %s" % path
    if remote_host:
        ret = remote.run_remote_cmd(cmd, params, ignore_status=False).stdout_text.strip()
    else:
        ret = process.run(cmd, shell=True).stdout_text.strip()
    if expected_ownership not in ret:
        test.fail(f"{path} ownership is not correct: {ret}")
    if len(file_list) > 0:
        for i in file_list:
            if i not in ret:
                test.fail(f"Not found {i}: {ret}.")
    else:
        if len(ret) != 2:
            test.fail(f"{path} is not empty: {ret}")


def prepare_iscsi_disk(params, test, block_dev):
    """
    Prepare iscsi disk for test

    """
    client_ip = params.get("client_ip")
    block_path = params.get("block_path")

    test.log.info("prepare iscsi disk")
    global dev_src
    global dev_target
    global target_luns
    dev_src = libvirt.setup_or_cleanup_iscsi(is_setup=True,
                                             is_login=True,
                                             image_size="1G",
                                             emulated_image="emulated-iscsi-1",
                                             portal_ip=client_ip)

    target_luns, _ = libvirt.setup_or_cleanup_iscsi(is_setup=True,
                                                    is_login=False,
                                                    image_size="1G",
                                                    emulated_image="emulated-iscsi-2",
                                                    portal_ip=client_ip)
    test.log.debug(f"target_luns: {target_luns}")
    dev_target = block_dev.iscsi_login_setup(client_ip, target_luns)
    test.log.debug(f"dev_src: {dev_src}")
    test.log.debug(f"dev_target: {dev_target}")

    cmd = f"fdisk -l {dev_src} && mkfs.ext4 -F {dev_src} && mkdir -p {block_path}"
    process.run(cmd, shell=True)
    if platform.platform().count('el10'):
        cmd = 'semanage fcontext -a -t virt_var_lib_t "{block_path}(/.*)?"'
        process.run(cmd, shell=True)
        remote.run_remote_cmd(cmd, params, ignore_status=False)

    cmd = f"fdisk -l {dev_target} && mkfs.ext4 -F {dev_target} && mkdir -p {block_path}"
    remote.run_remote_cmd(cmd, params, ignore_status=False)


def run(test, params, env):
    """
    Test migration for vm with vtpm state on block dev.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    def setup_auto_create_dir():
        """
        Setup steps for auto_create_dir case

        """
        block_path = params.get("block_path")
        server_ip = params.get("server_ip")
        server_user = params.get("server_user", "root")
        server_pwd = params.get("server_pwd")
        old_ownership = params.get("old_ownership")

        test.log.info("Setup steps for auto_create_dir.")
        global dev_src
        global dev_target
        utils_disk.mount(dev_src, block_path)
        cmd = f"restorecon -Rv {block_path}"
        process.run(cmd, shell=True)

        server_session = remote.wait_for_login("ssh", server_ip, "22", server_user, server_pwd, r"[\#\$]\s*$")
        utils_disk.mount(dev_target, block_path, session=server_session)
        cmd = f"restorecon -Rv {block_path}"
        remote.run_remote_cmd(cmd, params, ignore_status=False)

        check_ownership(block_path, old_ownership, test)


    def setup_manual_create_dir():
        """
        Setup steps for manual_create_dir case

        """
        test.log.info("Setup steps for manual_create_dir.")

    def setup_auto_create_file():
        """
        Setup steps for auto_create_file

        """
        test.log.info("Setup steps for auto_create_file.")

    def setup_test():
        """
        Setup steps for auto_create_dir case

        """
        test_case = params.get("test_case")
        block_dir = params.get("block_dir")
        file_list = params.get("file_list")
        new_ownership = params.get("new_ownership")

        test.log.info("Setup steps for auto_create_dir.")
        prepare_iscsi_disk(params, test, block_dev)
        migration_obj.setup_connection()
        setup_vtpm(params, test, vm, migration_obj)

        if test_case == "auto_create_dir":
            setup_auto_create_dir()
        elif test_case == "manual_create_dir":
            setup_manual_create_dir()
        elif test_case == "auto_create_file":
            setup_auto_create_file()

        check_ownership(block_dir, new_ownership, test, file_list=file_list)
        check_vtpm_func(params, vm, test, remote=False)

    def verify_test():
        """
        Verify steps

        """
        block_dir = params.get("block_dir")
        file_list = params.get("file_list")
        new_ownership = params.get("new_ownership")
        block_path = params.get("block_path")

        test.log.info("Verify steps.")
        check_ownership(block_dir, new_ownership, test, remote_host=True, file_list=file_list)
        check_vtpm_func(params, vm, test, remote=True)

    def verify_test_again():
        """
        Verify again

        """
        file_list = params.get("file_list")

        test.log.info("Verify again.")
        check_ownership(block_dir, new_ownership, test, file_list=file_list)
        check_vtpm_func(params, vm, test, remote=True)
        virsh.destroy(vm_name, debug=True)
        if not vm.wait_for_shutdown():
            test.error('VM failed to shutdown in 60s')
        check_ownership(block_dir, old_ownership, test, file_list=file_list)

    def cleanup_test():
        """
        Cleanup steps

        """
        desturi = params.get("virsh_migrate_desturi")
        block_path = params.get("block_path")
        block_dir = params.get("block_dir")
        dst_secret_value_path = params.get("dst_secret_value_path")
        src_secret_value_path = params.get("src_secret_value_path")
        server_ip = params.get("server_ip")
        server_user = params.get("server_user", "root")
        server_pwd = params.get("server_pwd")
        client_ip = params.get("client_ip")
        test_case = params.get("test_case")

        test.log.info("Cleanup steps.")
        global dev_src
        global dev_target

        def _umount_dir(dir_path):
            utils_disk.umount(dev_src, dir_path)
            server_session = remote.wait_for_login("ssh", server_ip, "22", server_user, server_pwd, r"[\#\$]\s*$")
            utils_disk.umount(dev_target, dir_path, session=server_session)
            server_session.close()

        if test_case == "auto_create_dir":
            _umount_dir(block_path)
        else:
            _umount_dir(block_dir)

        global target_luns
        block_dev.iscsi_login_setup(client_ip, target_luns, is_login=False)
        libvirt.setup_or_cleanup_iscsi(is_setup=False, emulated_image="emulated-iscsi-2", portal_ip=client_ip)
        libvirt.setup_or_cleanup_iscsi(is_setup=False, emulated_image="emulated-iscsi-1", portal_ip=client_ip)

        if src_sec_uuid:
            virsh.secret_undefine(src_sec_uuid, debug=True, ignore_status=True)
        if dst_sec_uuid:
            virsh.secret_undefine(dst_sec_uuid, debug=True, ignore_status=True, uri=desturi)

        migration_obj.cleanup_connection()
        cmd = f"rm -rf {dst_secret_value_path}"
        remote.run_remote_cmd(cmd, params)
        process.run(cmd, shell=True, ignore_status=True)
        cmd = f"rm -rf {src_secret_value_path}"
        process.run(cmd, shell=True, ignore_status=True)
        cmd = f"rm -rf {block_path}"
        remote.run_remote_cmd(cmd, params)
        process.run(cmd, shell=True, ignore_status=True)

    vm_name = params.get("migrate_main_vm")
    src_sec_uuid = None
    dst_sec_uuid = None

    libvirt_version.is_libvirt_feature_supported(params)

    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)
    block_dev = utils_test.RemoteDiskManager(params)

    try:
        src_sec_uuid, dst_sec_uuid = migration_vtpm.set_secret(params)
        #if not base_steps.check_cpu_for_mig(params):
        #    base_steps.sync_cpu_for_mig(params)
        setup_test()
        migration_obj.run_migration()
        verify_test()
        migration_obj.run_migration_back()
        verify_test_again()
    finally:
        cleanup_test()
