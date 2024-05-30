from virttest import libvirt_version
from virttest import utils_vdpa
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_disk
from virttest.utils_libvirt import libvirt_vmxml

VIRSH_ARGS = {'debug': True, 'ignore_status': False}


def run(test, params, env):
    """
    Verify that a vm can have more than one vhost-vdpa backend disks,
    and both the two disks can be successfully written to/read from.
    """
    libvirt_version.is_libvirt_feature_supported(params)
    disk_attrs = eval(params.get("disk_attrs", "{}"))
    disk2_attrs = eval(params.get("disk2_attrs", "{}"))

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        test.log.info("TEST_STEP: Define a VM with vhost-vdpa disk.")
        test_env_obj = utils_vdpa.VDPASimulatorTest(sim_dev_module="vdpa_sim_blk", mgmtdev="vdpasim_blk")
        test_env_obj.setup(dev_num=2)
        vm_xml.VMXML.set_memoryBacking_tag(vm_name, access_mode="shared", hpgs=False)
        libvirt_vmxml.modify_vm_device(vm_xml.VMXML.new_from_dumpxml(vm_name), "disk", disk_attrs, 1)
        libvirt_vmxml.modify_vm_device(vm_xml.VMXML.new_from_dumpxml(vm_name), "disk", disk2_attrs, 2)

        vm.start()
        vm_session = vm.wait_for_login()

        test.log.info("TEST_STEP: Check r/w operations on vhost-vdpa disk.")
        new_disks = libvirt_disk.get_non_root_disk_names(vm_session)
        vm_session.close()
        for disk in new_disks:
            if not libvirt_disk.check_virtual_disk_io(vm, disk[0]):
                test.fail("Failed to check disk io for %s!" % disk[0])

    finally:
        bkxml.sync()
        test_env_obj.cleanup()
