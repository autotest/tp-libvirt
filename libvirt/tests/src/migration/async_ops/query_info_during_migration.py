from avocado.utils import process

from virttest import remote
from virttest import virsh

from virttest.utils_test import libvirt

from provider.migration import base_steps

source_loop_dev = None
target_loop_dev = None


def run(test, params, env):
    """
    This case is to verify that readonly commands domblkinfo/domblklist/domstats/dommemstat
    can be executed during migration.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    def setup_test():
        """
        Setup steps

        """
        def _setup_loop_dev(remote_host=False):
            """
            Setup a loop device for test

            :param remote_host: If True, set loop device on remote host
            """
            loop_dev = None
            cmd = "losetup --find"
            if remote_host:
                loop_dev = remote.run_remote_cmd(cmd, params, ignore_status=False).stdout_text.strip()
            else:
                loop_dev = process.run(cmd, shell=True).stdout_text.strip()
            test.log.debug(f"loop dev: {loop_dev}")

            cmd = f"losetup {loop_dev} {block_device}"
            if remote_host:
                remote.run_remote_cmd(cmd, params, ignore_status=False)
            else:
                process.run(cmd, shell=True)
            return loop_dev

        target_dev = params.get("target_dev")
        block_device = params.get("block_device")

        migration_obj.setup_connection()
        libvirt.create_local_disk("file", block_device, '1', "qcow2")

        global source_loop_dev
        source_loop_dev = _setup_loop_dev()
        global target_loop_dev
        target_loop_dev = _setup_loop_dev(remote_host=True)

        ret = virsh.attach_disk(vm_name, source_loop_dev, target_dev, debug=True)
        libvirt.check_exit_status(ret)

    def cleanup_test():
        """
        Cleanup steps

        """
        migration_obj.cleanup_connection()
        global source_loop_dev
        if source_loop_dev:
            process.run(f"losetup -d {source_loop_dev}", shell=True)
        global target_loop_dev
        if target_loop_dev:
            remote.run_remote_cmd(f"losetup -d {target_loop_dev}", params)

    vm_name = params.get("migrate_main_vm")

    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)

    try:
        setup_test()
        migration_obj.run_migration()
        migration_obj.verify_default()
    finally:
        cleanup_test()
