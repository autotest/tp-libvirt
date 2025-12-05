import os
import ast

from avocado.utils import process

from virttest import libvirt_version
from virttest import remote
from virttest import virsh
from virttest import utils_disk
from virttest import data_dir

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.migration import base_steps


def start_vhost_sock_service_in_source(start_sock_service_cmd, image_path, sock_path):
    """
    Start one vhost sock service in source host.

    :param start_sock_service_cmd: command to start vhost service
    :param image_path: image file path
    :param sock_path: sock file path
    """
    # Create backend image in source host
    libvirt.create_local_disk("file", image_path, size="100M")
    chown_cmd = "chown qemu:qemu %s" % image_path
    process.run(chown_cmd, ignore_status=False, shell=True)
    # Start vhost sock service in source host
    process.run(start_sock_service_cmd, ignore_status=False, shell=True).stdout_text.strip()
    # Set SELinux context in source host
    ch_seccontext_cmd = "chcon -t svirt_image_t %s" % sock_path
    process.run(ch_seccontext_cmd, ignore_status=False, shell=True)
    set_bool_mmap_cmd = "setsebool domain_can_mmap_files 1"
    process.run(set_bool_mmap_cmd, ignore_status=False, shell=True)


def start_vhost_sock_service_in_remote(start_sock_service_cmd, image_path, sock_path, params):
    """
    Prepare and start one vhost sock service in remote host.

    :param start_sock_service_cmd: command to start vhost service
    :param image_path: image file path
    :param sock_path: sock file path
    :param params: test parameters
    """
    remote.run_remote_cmd(f"mkdir -p {os.path.dirname(image_path)}", params, ignore_status=True)
    # Create backend image in remote host
    remote_create_cmd = f"dd if=/dev/zero of={image_path} bs=1M count=100 && chown qemu:qemu {image_path}"
    remote.run_remote_cmd(remote_create_cmd, params, ignore_status=False)
    # Start vhost sock service in remote host
    remote.run_remote_cmd(start_sock_service_cmd, params, ignore_status=False)
    # Set SELinux context in remote host
    remote_selinux_cmd = f"chcon -t svirt_image_t {sock_path} && setsebool domain_can_mmap_files 1"
    remote.run_remote_cmd(remote_selinux_cmd, params, ignore_status=False)


def run(test, params, env):
    """
    Test vhostuser disk migration.

    1.Prepare vhostuser disk and start the domain.
    2.Perform migration operation.
    3.Verify vhostuser disk after migration.
    """

    def setup_test():
        """
        Setup steps before migration
        """
        nonlocal image_path, sock_path

        test.log.info("Setup steps for vhostuser disk migration.")

        sock_path = params.get("source_file", "/tmp/vhost.sock")
        image_path = data_dir.get_data_dir() + '/test.img'
        disk_dict = ast.literal_eval(params.get("disk_dict", "{}"))
        vm_attrs = ast.literal_eval(params.get("vm_attrs", "{}"))

        # Define start_sock_service_cmd
        start_sock_service_cmd = (
            'systemd-run --uid qemu --gid qemu /usr/bin/qemu-storage-daemon'
            ' --blockdev \'{"driver":"file","filename":"%s","node-name":"libvirt-1-storage","auto-read-only":true,"discard":"unmap"}\''
            ' --blockdev \'{"node-name":"libvirt-1-format","read-only":false,"driver":"raw","file":"libvirt-1-storage"}\''
            ' --export vhost-user-blk,id=vhost-user-blk0,node-name=libvirt-1-format,addr.type=unix,addr.path=%s,writable=on'
            ' --chardev stdio,mux=on,id=char0; sleep 3'
            % (image_path, sock_path))

        # Start vhost service in source host
        start_vhost_sock_service_in_source(start_sock_service_cmd, image_path, sock_path)
        # Start vhost service in remote host
        start_vhost_sock_service_in_remote(start_sock_service_cmd, image_path, sock_path, params)
        # Setup migration connection
        migration_obj.setup_connection()
        # Prepare the VM with memory backing and vhostuser disk.
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        vmxml.setup_attrs(**vm_attrs)
        disk_obj = libvirt_vmxml.create_vm_device_by_type("disk", disk_dict)
        test.log.debug("vhostuser disk xml is:\n%s" % disk_obj)
        vmxml.add_device(disk_obj)
        vmxml.sync()
        base_steps.sync_cpu_for_mig(params)
        vm.start()
        vm.wait_for_login().close()

        # Check if vhostuser disk is accessible in VM
        if "vhostuser" not in virsh.dumpxml(vm_name).stdout_text:
            test.fail("Check vhostuser disk in VM failed")

        test.log.info("Setup completed successfully.")

    def verify_test():
        """
        Verify steps after migration

        """
        test.log.info("Verify steps after vhostuser disk migration.")

        device_target = params.get("target_dev", "vdb")
        desturi = params.get("virsh_migrate_desturi")

        # Switch to destination host
        backup_uri, vm.connect_uri = vm.connect_uri, desturi
        vm.cleanup_serial_console()
        vm.create_serial_console()
        vm_session = vm.wait_for_serial_login(timeout=120)

        try:
            # Verify vhostuser disk is still accessible after migration
            output = vm_session.cmd_output("lsblk")
            test.log.debug("lsblk output after migration: %s", output)
            if device_target not in output:
                test.fail(f'Vhostuser disk device {device_target} not found in VM after migration')
            # Write data to the disk to ensure it's working
            utils_disk.dd_data_to_vm_disk(vm_session, "/dev/%s" % device_target)
            test.log.info(f"Vhostuser disk {device_target} is accessible after migration")

        finally:
            vm_session.close()

        # Restore original connection URI
        vm.connect_uri = backup_uri

        # Run default migration verification
        migration_obj.verify_default()

        test.log.info("Verification completed successfully.")

    def cleanup_test():
        """
        Cleanup steps for cases

        """
        test.log.info("Cleanup steps for vhostuser disk migration.")
        if vm.is_alive():
            vm.destroy(gracefully=False)
        vmxml_backup.sync()

        migration_obj.cleanup_connection()

        # Cleanup on remote host
        remote.run_remote_cmd("pkill -f qemu-storage-daemon", params, ignore_status=True)
        remote.run_remote_cmd(f"rm -rf {sock_path} {image_path}", params, ignore_status=True)

        # Kill all qemu-storage-daemon process on local host
        process.run("pkill -f qemu-storage-daemon", ignore_status=True, shell=True)

        # Clean up images
        for file_path in [image_path, sock_path]:
            if os.path.exists(file_path):
                os.remove(file_path)

        test.log.info("Cleanup completed successfully.")

    libvirt_version.is_libvirt_feature_supported(params)
    vm_name = params.get("migrate_main_vm")
    vm = env.get_vm(vm_name)

    # Initialize variables
    image_path = None
    sock_path = None

    # Back up xml file.
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    # Migration object
    migration_obj = base_steps.MigrationBase(test, vm, params)

    try:
        setup_test()
        migration_obj.run_migration()
        verify_test()

    finally:
        cleanup_test()
