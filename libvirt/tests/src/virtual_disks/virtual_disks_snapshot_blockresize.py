import logging

from virttest import virsh
from virttest import virt_vm
from virttest import utils_misc

from virttest.utils_libvirt import libvirt_disk

from virttest.libvirt_xml import vm_xml

LOG = logging.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test virsh blockresize on backing chain element.
    1.Start VM
    2.Prepare multiple snapshots(backing chain)
    3.Execute virsh blockresize on back chain element
    4.Check block virtual size after operation accomplished
    5.Clean up test environment
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    def check_resized_block():
        """
        Check block resize result

        """
        blockresize_path = external_snapshot_disks[-1]
        blockresize_size = params.get("size")
        virsh.blockresize(vm_name, blockresize_path, "%sMB" % blockresize_size, ignore_status=False)
        disk_info = utils_misc.get_image_info(blockresize_path)
        LOG.debug("disk info from qemu-img: %s", disk_info)
        if disk_info['vsize'] != (int)(blockresize_size) * 1000000:
            test.fail("Get unexpected vsize value:%s" % disk_info['vsize'])

    # Disk specific attributes.
    status_error = "yes" == params.get("status_error")
    device_target = params.get("virt_disk_device_target")
    snapshot_name = params.get("snapshot_name")
    snapshot_take = int(params.get("snapshot_take", "4"))

    external_snapshot_disks = []

    # Back up xml file.
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    try:
        vm.wait_for_login().close()
        # Cleanup snapshots if exists
        libvirt_disk.cleanup_snapshots(vm, external_snapshot_disks)
        external_snapshot_disks = libvirt_disk.make_external_disk_snapshots(vm, device_target, snapshot_name, snapshot_take)
    except virt_vm.VMStartError as e:
        test.fail("VM failed to start."
                  "Error: %s" % str(e))
    else:
        check_resized_block()
    finally:
        # Clean up snapshots
        libvirt_disk.cleanup_snapshots(vm, external_snapshot_disks)
        if vm.is_alive():
            vm.destroy(gracefully=False)
        vmxml_backup.sync()
