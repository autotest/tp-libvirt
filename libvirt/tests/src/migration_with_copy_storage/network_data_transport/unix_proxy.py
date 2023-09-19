import os

from six import itervalues

from avocado.utils import process

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_disk
from virttest.utils_libvirt import libvirt_vmxml

from provider.migration import base_steps


def prepare_disks(params, vm, vm_name, test):
    """
    Prepare disk

    :param params: Dictionary with the test parameter
    :param vm: vm object
    :param vm_name: vm name
    :param test: test object
    """
    disk_format = params.get("disk_format", "qcow2")
    disk_num = eval(params.get("disk_num", "1"))
    blk_source = vm.get_first_disk_devices()['source']
    disk_dict = eval(params.get("disk_dict"))

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    for num in range(2, disk_num+1):
        disk_path = os.path.join(os.path.dirname(blk_source),
                                 "mig_test%s.img" % str(num))
        disk_dict.update({"source": {"attrs": {"file": "%s" % disk_path}}})
        if os.path.exists(disk_path):
            os.remove(disk_path)
        libvirt_disk.create_disk("file", disk_format=disk_format, path=disk_path)
        target_dev = 'vd' + chr(num + ord('a') - 1)
        disk_dict.update({"target": {"dev": "%s" % target_dev}})
        test.log.debug("disk_dict: %s", disk_dict)
        vmxml.add_device(libvirt_vmxml.create_vm_device_by_type("disk", disk_dict))
        vmxml.sync()
    vm.start()
    vm.wait_for_login().close()


def cleanup_disks_local(vm):
    """
    Cleanup disks on local host

    :param vm: vm object
    """
    all_vm_disks = vm.get_blk_devices()
    blk_source = vm.get_first_disk_devices()['source']
    for disk in list(itervalues(all_vm_disks)):
        disk_path = disk.get("source")
        if disk_path != blk_source:
            cmd = "rm -f %s" % disk_path
            process.run(cmd, ignore_status=False, shell=True)


def run(test, params, env):
    """
    Test VM live migration with copy storage - network data transport - UNIX+Proxy.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    vm_name = params.get("migrate_main_vm")
    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)

    try:
        migration_obj.setup_connection()
        prepare_disks(params, vm, vm_name, test)
        base_steps.prepare_disks_remote(params, vm)
        migration_obj.run_migration()
        migration_obj.verify_default()
    finally:
        base_steps.cleanup_disks_remote(params, vm)
        cleanup_disks_local(vm)
        migration_obj.cleanup_connection()
