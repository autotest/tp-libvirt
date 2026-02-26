import ast

from virttest import remote
from virttest import utils_disk
from virttest import utils_test
from virttest import utils_iptables

from virttest.utils_libvirt import libvirt_disk
from virttest.utils_test import libvirt

from provider.migration import base_steps
from provider.virtual_disk import disk_base


def setup_block_remote(params, test):
    """
    Setup iSCSI target on remote host.

    :param params: test parameters
    :param test: test object
    """
    test.log.info("Setting up iSCSI target on remote host.")
    client_ip = params.get("client_ip")
    vg_name = params.get("vg_name", "vg0")
    lv_name = params.get("lv_name", "lv0")
    lv_size = params.get("lv_size", "200M")
    target_emulate_image = params.get("target_emulate_image", "emulated-iscsi2")
    block_dev = utils_test.RemoteDiskManager(params)
    iscsi_target, _ = libvirt.setup_or_cleanup_iscsi(is_setup=True,
                                                     is_login=False,
                                                     emulated_image=target_emulate_image,
                                                     portal_ip=client_ip)

    remote_device = block_dev.iscsi_login_setup(client_ip, iscsi_target)
    clear_dm_dev = (
        f"dmsetup ls --noheadings | awk '/^{vg_name}-/ {{print $1}}' "
        f"| xargs -r dmsetup remove --force; rm -rf /dev/{vg_name}"
    )
    remote.run_remote_cmd(clear_dm_dev, params, ignore_status=True)
    block_dev.create_vg(vg_name, remote_device)
    block_dev.create_image("lvm",
                           size=lv_size,
                           vgname=vg_name,
                           lvname=lv_name,
                           sparse=False,
                           timeout=60)


def run(test, params, env):
    """
    Test migration with different disk types (iSCSI LV and NBD).
    """
    def setup_test():
        """
        Setup steps for migration with different disk types.
        """
        test.log.info("Setting up %s disk test environment for guest.", disk_type)
        migration_obj.setup_connection()
        disk_obj.new_image_path = disk_obj.add_vm_disk(disk_type, disk_dict)
        if not vm.is_alive():
            vm.start()

        vm_session = vm.wait_for_login()
        source_disk_name, _ = libvirt_disk.get_non_root_disk_name(vm_session)
        utils_disk.dd_data_to_vm_disk(vm_session, source_disk_name)
        vm_session.close()

        if disk_type == "block":
            setup_block_remote(params, test)

        if disk_type == "nbd":
            firewall_cmd.add_port(nbd_server_port, "tcp", permanent=True)

    def cleanup_test():
        """
        Cleanup steps for migration with different disk types.
        """
        test.log.info("Cleanup steps for migration with different disk types.")
        migration_obj.cleanup_connection()
        disk_obj.cleanup_disk_preparation(disk_type)
        if disk_type == "block":
            remote.run_remote_cmd("iscsiadm -m session -u", params, ignore_status=True)
            libvirt.setup_or_cleanup_iscsi(is_setup=False,
                                           emulated_image=target_emulate_image,
                                           portal_ip=client_ip)
        if disk_type == "nbd":
            firewall_cmd.remove_port(nbd_server_port, "tcp", permanent=True)

    vm_name = params.get("migrate_main_vm")
    vm = env.get_vm(vm_name)
    disk_type = params.get("disk_type")
    client_ip = params.get("client_ip")
    target_emulate_image = params.get("target_emulate_image", "emulated-iscsi2")
    disk_dict = ast.literal_eval(params.get("disk_dict", "{}"))
    nbd_server_port = params.get("nbd_server_port", "10809")

    base_steps.sync_cpu_for_mig(params)
    migration_obj = base_steps.MigrationBase(test, vm, params)
    disk_obj = disk_base.DiskBase(test, vm, params)
    firewall_cmd = utils_iptables.Firewall_cmd()

    try:
        setup_test()
        migration_obj.run_migration()
        migration_obj.verify_default()
    finally:
        cleanup_test()
