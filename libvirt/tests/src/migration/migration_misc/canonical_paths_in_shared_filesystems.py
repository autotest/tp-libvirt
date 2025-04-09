import os

from avocado.utils import process

from virttest import libvirt_remote
from virttest import remote
from virttest import utils_config
from virttest import utils_disk
from virttest import utils_misc
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_bios
from virttest.utils_test import libvirt

from provider.guest_os_booting import guest_os_booting_base as guest_os
from provider.migration import base_steps


def setup_for_shared_filesystems(params, test):
    """
    Setup for shared filesystems

    :param params: dict, test parameters
    :param test: test object
    """
    export_dir = params.get("export_dir")
    nfs_server_ip = params.get("nfs_server_ip")
    nfs_images_path = params.get("nfs_images_path")
    nfs_nvram_path = params.get("nfs_nvram_path")
    nfs_swtpm_path = params.get("nfs_swtpm_path")
    images_path = params.get("images_path")
    nvram_path = params.get("nvram_path")
    swtpm_path = params.get("swtpm_path")
    nfs_mount_options = params.get("nfs_mount_options")
    server_ip = params.get("server_ip")
    server_user = params.get("server_user", "root")
    server_pwd = params.get("server_pwd")
    mount_images_path = params.get("mount_images_path")
    mount_nvram_path = params.get("mount_nvram_path")
    mount_swtpm_path = params.get("mount_swtpm_path")

    if not os.path.exists(export_dir):
        os.mkdir(export_dir)

    mount_opt = "rw,no_root_squash,sync"
    res = libvirt.setup_or_cleanup_nfs(is_setup=True,
                                       is_mount=False,
                                       export_options=mount_opt,
                                       export_dir=export_dir)

    for path in [nfs_images_path, nfs_nvram_path, nfs_swtpm_path]:
        if not utils_misc.check_exists(path):
            process.run(f"mkdir -p {path}", shell=True, verbose=True)

    utils_disk.mount(nfs_images_path, images_path, options=nfs_mount_options)
    utils_disk.mount(nfs_nvram_path, nvram_path, options=nfs_mount_options)
    utils_disk.mount(nfs_swtpm_path, swtpm_path, options=nfs_mount_options)

    remote_session = remote.wait_for_login("ssh", server_ip, "22", server_user, server_pwd, r"[\#\$]\s*$")
    utils_disk.mount(mount_images_path, images_path, session=remote_session)
    utils_disk.mount(mount_nvram_path, nvram_path, session=remote_session)
    utils_disk.mount(mount_swtpm_path, swtpm_path, session=remote_session)
    remote_session.close()

    remote_obj = None
    local_obj = None
    qemu_conf_src = eval(params.get("qemu_conf_src", "{}"))
    qemu_conf_dest = params.get("qemu_conf_dest", "{}")
    qemu_conf_path = utils_config.LibvirtQemuConfig().conf_path
    remote_obj = libvirt_remote.update_remote_file(params, qemu_conf_dest, qemu_conf_path)
    local_obj = libvirt.customize_libvirt_config(qemu_conf_src, "qemu", remote_host=False,
                                                 extra_params=params)
    return (local_obj, remote_obj)


def cleanup_for_shared_filesystems(params, test):
    """
    Cleanup for shared filesystems

    :param params: dict, test parameters
    :param test: test object
    """
    images_path = params.get("images_path")
    nvram_path = params.get("nvram_path")
    swtpm_path = params.get("swtpm_path")
    server_ip = params.get("server_ip")
    server_user = params.get("server_user", "root")
    server_pwd = params.get("server_pwd")
    export_dir = params.get("export_dir")
    mount_images_path = params.get("mount_images_path")
    mount_nvram_path = params.get("mount_nvram_path")
    mount_swtpm_path = params.get("mount_swtpm_path")

    test.log.info("Cleanup for shared_filesystems.")
    remote_session = remote.wait_for_login("ssh", server_ip, "22", server_user, server_pwd, r"[\#\$]\s*$")
    utils_disk.umount(mount_images_path, images_path, session=remote_session)
    utils_disk.umount(mount_nvram_path, nvram_path, session=remote_session)
    utils_disk.umount(mount_swtpm_path, swtpm_path, session=remote_session)
    remote_session.close()

    for path in [images_path, nvram_path, swtpm_path]:
        process.run(f"umount {path}", shell=True, verbose=True)

    libvirt.setup_or_cleanup_nfs(is_setup=False)
    process.run(f"rm -rf {export_dir}", shell=True, ignore_status=True)


def run(test, params, env):
    """
    Migrate the guests with various tpm and firmware on canonical paths in shared_filesystems.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    def setup_test():
        """
        Setup steps for cases

        """
        images_path = params.get("images_path")
        tpm_args = eval(params.get("tpm_args", '{}'))
        loader_dict = eval(params.get("loader_dict", "{}"))

        test.log.info("Setup steps for cases.")

        nonlocal local_qemu_obj
        nonlocal remote_qemu_obj
        local_qemu_obj, remote_qemu_obj = setup_for_shared_filesystems(params, test)

        blk_source = vm.get_first_disk_devices()['source']
        process.run(f"cp {blk_source} {images_path}", shell=True, verbose=True)
        process.run("setsebool virt_use_nfs 1", shell=True, verbose=True)
        remote.run_remote_cmd("setsebool virt_use_nfs 1", params)

        vmxml.remove_all_device_by_type('tpm')
        if tpm_args:
            tpm_dev = libvirt.create_tpm_dev(tpm_args)
            vmxml.add_device(tpm_dev)
        vmxml.sync()

        if boot_type == "bios":
            os_dict = eval(params.get("os_dict"))
            vmxml.del_os()
            vmxml.setup_attrs(os=os_dict)
        elif boot_type == "statless_uefi":
            vmxml.os = libvirt_bios.remove_bootconfig_items_from_vmos(vmxml.os)
            vmxml.setup_attrs(os=loader_dict)
        else:
            vmxml.setup_attrs(os=loader_dict)
        vmxml.sync()
        virsh.dumpxml(vm_name, debug=True)
        migration_obj.setup_connection()
        vm.start()
        vm.wait_for_login().close()

    def verify_test_again():
        """
        Verify test again

        """
        test.log.info("Verify test again.")
        dargs = {"check_disk_on_dest": "no"}
        migration_obj.migration_test.post_migration_check([vm], dargs)

    def cleanup_test():
        """
        Cleanup steps for cases
        """
        test.log.info("Cleanup steps for cases.")
        migration_obj.cleanup_connection()
        cleanup_for_shared_filesystems(params, test)
        process.run("rm -rf /var/lib/libvirt/swtpm/*", shell=True, ignore_status=True)
        process.run("rm -rf /var/lib/libvirt/qemu/nvram/*", shell=True, ignore_status=True)
        nonlocal local_qemu_obj
        if local_qemu_obj:
            libvirt.customize_libvirt_config(None, config_type="qemu", remote_host=False,
                                             is_recover=True, extra_params=params,
                                             config_object=local_qemu_obj)
        nonlocal remote_qemu_obj
        if remote_qemu_obj:
            del remote_qemu_obj

    vm_name = guest_os.get_vm(params)
    boot_type = params.get("boot_type")
    params.update({"main_vm": vm_name})
    local_qemu_obj = None
    remote_qemu_obj = None

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    if boot_type == "bios":
        bkxml = vmxml.copy()
    migration_obj = base_steps.MigrationBase(test, vm, params)

    try:
        setup_test()
        migration_obj.run_migration()
        migration_obj.verify_default()
        migration_obj.run_migration_back()
        verify_test_again()
    finally:
        cleanup_test()
        if boot_type == "bios":
            bkxml.sync()
