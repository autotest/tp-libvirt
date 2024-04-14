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
    Verify that vm can be defined and started with vhost-vdpa backend disk,
    and disk I/O in guest can work
    """
    libvirt_version.is_libvirt_feature_supported(params)
    disk_attrs = eval(params.get("disk_attrs", "{}"))
    without_shared_memory = "yes" == params.get("without_shared_memory", "no")
    define_error = "yes" == params.get("define_error", "no")
    start_error = "yes" == params.get("start_error", "no")
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        test.log.info("TEST_STEP: Define a VM with vhostvdpa disk.")
        test_env_obj = utils_vdpa.VDPASimulatorTest(sim_dev_module="vdpa_sim_blk", mgmtdev="vdpasim_blk")
        test_env_obj.setup()
        if not without_shared_memory:
            vm_xml.VMXML.set_memoryBacking_tag(vm_name, access_mode="shared", hpgs=False)
        disk_dev = libvirt_vmxml.create_vm_device_by_type("disk", disk_attrs)
        vmxml_new = vm_xml.VMXML.new_from_dumpxml(vm_name)
        vmxml_new.add_device(disk_dev)

        test.log.debug(f'VMXML of {vm_name}:\n{vmxml_new}')
        cmd_result = virsh.define(vmxml_new.xml, debug=True)
        libvirt.check_exit_status(cmd_result, define_error)
        if define_error:
            return

        test.log.info("TEST_STEP: Start the VM.")
        cmd_result = virsh.start(vm_name)
        libvirt.check_exit_status(cmd_result, start_error)
        if start_error:
            return
        vm_session = vm.wait_for_login()

        test.log.info("TEST_STEP: Check VM's xml.")
        vdpa_device = vm_xml.VMXML.new_from_dumpxml(vm.name)\
            .devices.by_device_tag("disk")[-1]
        vdpa_dev_attrs = vdpa_device.fetch_attrs()
        test.log.debug(vdpa_dev_attrs)
        for k, v in disk_attrs.items():
            if k == "source":
                act_dev = vdpa_dev_attrs[k]["attrs"].get("dev")
                exp_dev = v["attrs"]["dev"]
                test.log.debug(f"Actual dev: {act_dev}")
                test.log.debug(f"Expected dev: {exp_dev}")
                if act_dev != exp_dev:
                    test.fail("Incorrect disk dev!")
            elif vdpa_dev_attrs[k] != v:
                test.fail("Failed to get expected disk attributes(%s) in live xml!"
                          "It should be %s." % (vdpa_dev_attrs[k], v))

        test.log.info("TEST_STEP: Check r/w operations on vhostvdpa disk.")
        new_disk = libvirt_disk.get_non_root_disk_name(vm_session)[0]
        if not libvirt_disk.check_virtual_disk_io(vm, new_disk):
            test.fail("Failed to check disk io for %s!" % new_disk)

    finally:
        bkxml.sync()
        test_env_obj.cleanup()
