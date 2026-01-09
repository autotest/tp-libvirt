import os
import ast

from virttest import libvirt_version
from virttest import remote
from virttest import virsh
from virttest import data_dir

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml

from provider.migration import base_steps


def start_vhost_sock_service(start_sock_service_cmd, image_path, params, runner):
    """
    Prepare and start vhost sock service in local/remote host.

    :param start_sock_service_cmd: command to start vhost service.
    :param image_path: image file path.
    :param params: the parameter for executing
    :param runner: a runner object on local/remote host.
    """
    sock_path = params.get("source_file", "/tmp/vhost.sock")
    remote.run_remote_cmd(f"mkdir -p {os.path.dirname(image_path)}", params, runner, ignore_status=True)
    remote_create_cmd = f"dd if=/dev/zero of={image_path} bs=1M count=100 && chown qemu:qemu {image_path}"
    remote.run_remote_cmd(remote_create_cmd, params, runner, ignore_status=False)
    remote.run_remote_cmd(start_sock_service_cmd, params, runner, ignore_status=False)
    remote_selinux_cmd = f"chcon -t svirt_image_t {sock_path} && setsebool domain_can_mmap_files 1"
    remote.run_remote_cmd(remote_selinux_cmd, params, runner, ignore_status=False)


def remove_vhost_sock_service(image_path, params, runner):
    """
    Remove the vhost sock service.
    """
    sock_path = params.get("source_file", "/tmp/vhost.sock")
    remote.run_remote_cmd("pkill -f qemu-storage-daemon", params, runner, ignore_status=True)
    remote.run_remote_cmd(f"rm -rf {sock_path} {image_path}", params, runner, ignore_status=True)


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
        test.log.info("Setup steps for vhostuser disk migration.")
        sock_path = params.get("source_file", "/tmp/vhost.sock")
        disk_dict = ast.literal_eval(params.get("disk_dict", "{}"))
        vm_attrs = ast.literal_eval(params.get("vm_attrs", "{}"))

        start_sock_service_cmd = (
            'systemd-run --uid qemu --gid qemu /usr/bin/qemu-storage-daemon'
            ' --blockdev \'{"driver":"file","filename":"%s","node-name":"libvirt-1-storage","auto-read-only":true,"discard":"unmap"}\''
            ' --blockdev \'{"node-name":"libvirt-1-format","read-only":false,"driver":"raw","file":"libvirt-1-storage"}\''
            ' --export vhost-user-blk,id=vhost-user-blk0,node-name=libvirt-1-format,addr.type=unix,addr.path=%s,writable=on'
            ' --chardev stdio,mux=on,id=char0; sleep 3'
            % (image_path, sock_path))

        start_vhost_sock_service(start_sock_service_cmd, image_path, params, local_runner)
        start_vhost_sock_service(start_sock_service_cmd, image_path, params, remote_runner)
        migration_obj.setup_connection()

        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        vmxml.setup_attrs(**vm_attrs)
        disk_obj = libvirt_vmxml.create_vm_device_by_type("disk", disk_dict)
        test.log.debug("vhostuser disk xml is:\n%s" % disk_obj)
        vmxml.add_device(disk_obj)
        vmxml.sync()
        base_steps.sync_cpu_for_mig(params)
        vm.start()
        vm.wait_for_login().close()

        if "vhostuser" not in virsh.dumpxml(vm_name).stdout_text:
            test.fail("Check vhostuser disk in VM failed")
        test.log.info("Setup completed successfully.")

    def cleanup_test():
        """
        Cleanup steps for cases

        """
        test.log.info("Cleanup steps for vhostuser disk migration.")
        migration_obj.cleanup_connection()
        remove_vhost_sock_service(image_path, params, local_runner)
        remove_vhost_sock_service(image_path, params, remote_runner)
        test.log.info("Cleanup completed successfully.")

    libvirt_version.is_libvirt_feature_supported(params)
    vm_name = params.get("migrate_main_vm")
    vm = env.get_vm(vm_name)
    client_ip = params.get("client_ip")
    client_pwd = params.get("client_pwd")
    server_ip = params.get("server_ip")
    server_pwd = params.get("server_pwd")
    server_user = params.get("server_user")
    image_path = data_dir.get_data_dir() + '/test.img'

    migration_obj = base_steps.MigrationBase(test, vm, params)
    local_runner = remote.RemoteRunner(host=client_ip, username=server_user, password=client_pwd)
    remote_runner = remote.RemoteRunner(host=server_ip, username=server_user, password=server_pwd)

    try:
        setup_test()
        migration_obj.run_migration()
        migration_obj.verify_default()

    finally:
        cleanup_test()
