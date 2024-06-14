# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0
#
#   Author: Liping Cheng <lcheng@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

from virttest import libvirt_version
from virttest import remote
from virttest import utils_vdpa

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_disk
from virttest.utils_libvirt import libvirt_vmxml

from provider.migration import base_steps


def setup_vhost_vdpa_simulator_disk(params, run_on_target, vdpa_obj):
    """
    Setup vhostvdpa disk

    :param params: dictionary with the test parameters
    :param run_on_target: remote runner object
    :param vdpa_obj: vdpa simulator object
    """
    vdpa_obj.setup()

    cmd = "modprobe vhost-vdpa"
    remote.run_remote_cmd(cmd, params, run_on_target)

    cmd = "modprobe vdpa-sim-blk"
    remote.run_remote_cmd(cmd, params, run_on_target)

    cmd = "vdpa dev add mgmtdev vdpasim_blk name blk0"
    remote.run_remote_cmd(cmd, params, run_on_target)


def cleanup_vhost_vdpa_simulator_disk(params, run_on_target, vdpa_obj):
    """
    Cleanup vhostvdpa disk

    :param params: dictionary with the test parameters
    :param run_on_target: remote runner object
    :param vdpa_obj: vdpa simulator object
    """
    vdpa_obj.cleanup()

    cmd = "modprobe -r vdpa-sim-blk"
    remote.run_remote_cmd(cmd, params, run_on_target)

    cmd = "modprobe -r vhost-vdpa"
    remote.run_remote_cmd(cmd, params, run_on_target)


def run(test, params, env):
    """
    To verify that migrate vm with copying vhostvdpa backend disk can succeed.

    :param test: test object
    :param params: dictionary with the test parameters
    :param env: dictionary with test environment.
    """
    def setup_test():
        """
        Setup steps

        """
        vhost_vdpa_disk = params.get("vhost_vdpa_disk")
        disk_dict = eval(params.get("disk_dict"))

        test.log.info("Setup steps.")
        migration_obj.setup_connection()

        setup_vhost_vdpa_simulator_disk(params, run_on_target, vdpa_obj)
        # Set shared memory
        vm_xml.VMXML.set_memoryBacking_tag(vm_name, access_mode="shared", hpgs=False)
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        vmxml.add_device(libvirt_vmxml.create_vm_device_by_type("disk", disk_dict))
        vmxml.sync()
        vm.start()
        vm_session = vm.wait_for_login()
        new_disk = libvirt_disk.get_non_root_disk_name(vm_session)[0]
        vm_session.close()
        if not libvirt_disk.check_virtual_disk_io(vm, new_disk):
            test.fail("Failed to check disk io for %s!" % new_disk)

    def cleanup_test():
        """
        Cleanup steps

        """
        test.log.info("Cleanup steps.")
        if vm.is_alive():
            vm.destroy()
        cleanup_vhost_vdpa_simulator_disk(params, run_on_target, vdpa_obj)
        migration_obj.cleanup_connection()

    vm_name = params.get("migrate_main_vm")
    server_ip = params.get("server_ip")
    server_user = params.get("server_user", "root")
    server_pwd = params.get("server_pwd")

    libvirt_version.is_libvirt_feature_supported(params)
    run_on_target = remote.RemoteRunner(host=server_ip, username=server_user, password=server_pwd)
    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)
    vdpa_obj = utils_vdpa.VDPASimulatorTest(sim_dev_module="vdpa_sim_blk", mgmtdev="vdpasim_blk")

    try:
        setup_test()
        migration_obj.run_migration()
        migration_obj.verify_default()
    finally:
        cleanup_test()
