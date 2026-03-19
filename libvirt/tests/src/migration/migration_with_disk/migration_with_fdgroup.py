import os
import ast

from avocado.utils import process

from virttest import libvirt_version
from virttest import remote
from virttest import virsh
from virttest import utils_package
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.migration import base_steps

cleanup_files = []


def prepare_fd_disks(params, test, vmxml):
    """
    Prepare file descriptor disks and add to VM XML

    :param params: test parameters
    :param test: test object
    :param vmxml: VM XML object
    :return: tuple containing file paths (file_vdb, file_vdc)
    """
    disk_files = {}
    for disk_dev in ["vdb", "vdc"]:
        file_path = params.get(f"source_file_path_{disk_dev}")
        disk_format = params.get(f"target_format_{disk_dev}", "raw")
        test.log.info("Creating FD disk image on NFS share: %s (%s)", file_path, disk_format)
        libvirt.create_local_disk("file", file_path, 1, disk_format)
        cleanup_files.append(file_path)
        disk_files[disk_dev] = file_path

        disk_dict = ast.literal_eval(params.get(f"disk_dict_{disk_dev}", "{}"))
        disk_obj = libvirt_vmxml.create_vm_device_by_type("disk", disk_dict)
        test.log.debug("FD disk %s xml is:\n%s", disk_dev, disk_obj)
        vmxml.add_device(disk_obj)
    vmxml.sync()
    return disk_files["vdb"], disk_files["vdc"]


def associate_fds(vm_name, params, test, on_dest=False, runner=None):
    """
    Associate file descriptors with domain

    :param vm_name: VM name
    :param params: test parameters
    :param test: test object
    :param on_dest: True if running on destination, False for source
    :param runner: RemoteRunner for dest host (required when on_dest=True)
    """
    fdgroup_vdb = params.get("fdgroup_name_vdb")
    fdgroup_vdc = params.get("fdgroup_name_vdc")
    fd_id_vdb = params.get("file_descriptor_id_vdb")
    fd_id_vdc = params.get("file_descriptor_id_vdc")
    file_vdb = params.get("source_file_path_vdb")
    file_vdc = params.get("source_file_path_vdc")
    flag = params.get("flag", "")

    if on_dest:
        cmd = (f'nohup sh -c \'(echo "dom-fd-associate {vm_name} {fdgroup_vdb} {fd_id_vdb}{flag}"; '
               f'echo "dom-fd-associate {vm_name} {fdgroup_vdc} {fd_id_vdc}{flag}"; '
               f'tail -f /dev/null) | virsh\' {fd_id_vdb}<>{file_vdb} {fd_id_vdc}<>{file_vdc} '
               f'> /dev/null 2>&1 &')
        test.log.debug("Running FD association on dest: %s", cmd)
        remote.run_remote_cmd(cmd, params, runner, ignore_status=False)
    else:
        cmd = (f'virsh "dom-fd-associate {vm_name} {fdgroup_vdb} {fd_id_vdb}{flag}; '
               f'dom-fd-associate {vm_name} {fdgroup_vdc} {fd_id_vdc}{flag}; '
               f'start {vm_name}" {fd_id_vdb}<>{file_vdb} {fd_id_vdc}<>{file_vdc}')
        test.log.debug("Running command on source: %s", cmd)
        result = process.run(cmd, ignore_status=True, shell=True)
        if result.exit_status != 0:
            test.fail(f"Failed to associate FDs and start VM: {result.stderr_text}")


def verify_fd_open(file_paths, test, on_remote=False, params=None, runner=None):
    """
    Verify that virsh and virtqemud processes have the FDs open

    :param file_paths: list of file paths to check
    :param test: test object
    :param on_remote: True if checking on remote host, False for local
    :param params: test parameters (required when on_remote=True)
    :param runner: RemoteRunner for remote host (required when on_remote=True)
    """
    for file_path in file_paths:
        cmd = f"lsof {file_path} | grep -E 'qemu' || true"

        if on_remote:
            result = remote.run_remote_cmd(cmd, params, runner, ignore_status=True)
            stdout = result.stdout_text if hasattr(result, 'stdout_text') else result
        else:
            result = process.run(cmd, ignore_status=True, shell=True)
            stdout = result.stdout_text

        if not stdout.strip():
            location = "remote" if on_remote else "local"
            test.fail(f"No qemu process found with FD open for {file_path} on {location} host")

        test.log.debug("FD verification for %s on %s:\n%s",
                       file_path, "remote" if on_remote else "local", stdout)


def prepare_dest_vm(vm_name, vmxml, params, remote_runner, test):
    """
    Prepare VM on destination host for migration

    :param vm_name: VM name
    :param vmxml: VM XML object
    :param params: test parameters
    :param remote_runner: RemoteRunner for dest host
    :param test: test object
    """
    virsh_dargs = {'uri': params.get("virsh_migrate_desturi"),
                   'remote_ip': params.get("server_ip"),
                   'remote_user': params.get("server_user"),
                   'remote_pwd': params.get("server_pwd"),
                   'unprivileged_user': None,
                   'ssh_remote_auth': True}

    virsh.define(vmxml.xml, ignore_status=False, **virsh_dargs)
    associate_fds(vm_name, params, test, on_dest=True, runner=remote_runner)


def run(test, params, env):
    """
    Test file descriptor disk migration.

    1. Prepare file descriptor disks on NFS share
    2. Open FDs and associate with domain on source
    3. Start domain on source
    4. Verify FDs are open by virsh/virtqemud
    5. Prepare FDs on destination (open same NFS files)
    6. Perform migration
    7. Verify migration succeeded
    """

    def setup_test():
        """
        Setup steps before migration
        """
        test.log.info("Setup steps for fdgroup migration.")
        cleanup_files.clear()
        migration_obj.setup_connection()

        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        file_vdb, file_vdc = prepare_fd_disks(params, test, vmxml)

        base_steps.sync_cpu_for_mig(params)
        vmxml.sync()
        associate_fds(vm_name, params, test, on_dest=False)
        vm.wait_for_login().close()

        verify_fd_open([file_vdb, file_vdc], test)

        prepare_dest_vm(vm_name, vmxml, params, remote_runner, test)
        verify_fd_open([file_vdb, file_vdc], test, on_remote=True, params=params, runner=remote_runner)
        test.log.info("Setup completed successfully.")

    def cleanup_test():
        """
        Cleanup steps for cases
        """
        test.log.info("Cleanup steps for fdgroup migration.")
        migration_obj.cleanup_connection()

        test.log.info("Cleaning up FD disk images from NFS share")
        for file_path in cleanup_files:
            if os.path.exists(file_path):
                os.remove(file_path)

        cleanup_cmd = f"pkill -f 'virsh.*dom-fd-associate.*{vm_name}'"
        remote.run_remote_cmd(cleanup_cmd, params, remote_runner, ignore_status=True)
        test.log.info("Cleanup completed successfully.")

    libvirt_version.is_libvirt_feature_supported(params)
    vm_name = params.get("migrate_main_vm")
    vm = env.get_vm(vm_name)
    server_ip = params.get("server_ip")
    server_pwd = params.get("server_pwd")
    server_user = params.get("server_user", "root")

    migration_obj = base_steps.MigrationBase(test, vm, params)
    remote_runner = remote.RemoteRunner(host=server_ip, username=server_user, password=server_pwd)

    try:
        if not utils_package.package_install(["lsof"]):
            test.cancel("Failed to install dependency package lsof on host.")
        remote_session = remote.remote_login("ssh", server_ip, "22", server_user, server_pwd,
                                             r"[\#\$]\s*$")
        try:
            if not utils_package.package_install(["lsof"], remote_session):
                test.cancel("Failed to install dependency package lsof on destination host.")
        finally:
            remote_session.close()
        setup_test()
        migration_obj.run_migration()
        migration_obj.verify_default()

    finally:
        cleanup_test()
