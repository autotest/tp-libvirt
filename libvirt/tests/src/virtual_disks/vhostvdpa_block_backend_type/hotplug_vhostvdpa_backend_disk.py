from virttest import libvirt_version
from virttest import utils_vdpa
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_disk
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

VIRSH_ARGS = {'debug': True, 'ignore_status': False}


def run(test, params, env):
    """
    Hotplug/unplug vhost-vdpa backend disk
    """
    libvirt_version.is_libvirt_feature_supported(params)
    disk_attrs = eval(params.get("disk_attrs", "{}"))
    status_error = "yes" == params.get("status_error", "no")
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        test.log.info("TEST_SETUP: Prepare a simulated vhost-vdpa disk on host.")
        test_env_obj = utils_vdpa.VDPASimulatorTest(
            sim_dev_module="vdpa_sim_blk", mgmtdev="vdpasim_blk")
        test_env_obj.setup()

        test.log.info("TEST_STEP: Define a VM with shared memory.")
        vm_xml.VMXML.set_memoryBacking_tag(vm_name, access_mode="shared", hpgs=False)
        vm.start()
        vm_session = vm.wait_for_login()

        test.log.info("TEST_STEP: Hotplug a vhost-vdpa disk to VM.")
        disk_dev = libvirt_vmxml.create_vm_device_by_type("disk", disk_attrs)
        cmd_result = virsh.attach_device(vm_name, disk_dev.xml, debug=True)
        libvirt.check_exit_status(cmd_result, status_error)
        if status_error:
            return

        test.log.info("TEST_STEP: Check VM's xml.")
        vdpa_device = vm_xml.VMXML.new_from_dumpxml(vm.name)\
            .devices.by_device_tag("disk")[-1]
        vdpa_dev_attrs = vdpa_device.fetch_attrs()
        test.log.debug(vdpa_dev_attrs)
        for k, v in disk_attrs.items():
            if k == "source":
                act_dev = vdpa_dev_attrs[k]["attrs"].get("dev")
                exp_dev = v["attrs"]["dev"]
                if act_dev != exp_dev:
                    test.fail("Incorrect disk dev! Expected: %s, Actual: %s."
                              % (exp_dev, act_dev))
            elif vdpa_dev_attrs[k] != v:
                test.fail("Failed to get expected disk attributes(%s) in live xml!"
                          "It should be %s." % (vdpa_dev_attrs[k], v))

        test.log.info("TEST_STEP: Check r/w operations on vhostvdpa disk.")
        new_disk = libvirt_disk.get_non_root_disk_name(vm_session)[0]
        if not libvirt_disk.check_virtual_disk_io(vm, new_disk):
            test.fail("Failed to check disk io for %s!" % new_disk)

        test.log.info("TEST_STEP: Hotunplug a vhost-vdpa disk from VM.")
        target_dev = disk_attrs['target']['dev']
        virsh.detach_disk(vm_name, target_dev, **VIRSH_ARGS)
        vm_disks = vm_xml.VMXML.new_from_dumpxml(vm.name).devices.by_device_tag("disk")
        if len(vm_disks) != 1:
            test.fail("There should be only one disk but got %s." % vm_disks)
    finally:
        bkxml.sync()
        test_env_obj.cleanup()
