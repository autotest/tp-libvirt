from virttest import libvirt_version
from virttest import utils_disk
from virttest import utils_misc
from virttest import utils_vdpa
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_disk
from virttest.utils_libvirt import libvirt_vmxml

VIRSH_ARGS = {'debug': True, 'ignore_status': False}


def run(test, params, env):
    """
    Verify that blockcopy a disk of vhostvdpa backend can succeed
    """
    libvirt_version.is_libvirt_feature_supported(params)
    disk_attrs = eval(params.get("disk_attrs", "{}"))
    bc_disk_attrs = eval(params.get("bc_disk_attrs", "{}"))
    disk_target = params.get("disk_target", "vdb")

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        test.log.info("TEST_STEP: Prepare two simulated vhost-vdpa disks on host.")
        test_env_obj = utils_vdpa.VDPASimulatorTest(sim_dev_module="vdpa_sim_blk", mgmtdev="vdpasim_blk")
        test_env_obj.setup(dev_num=2)

        test.log.info("TEST_STEP: Define a VM with vhost-vdpa disk.")
        vm_xml.VMXML.set_memoryBacking_tag(vm_name, access_mode="shared", hpgs=False)
        libvirt_vmxml.modify_vm_device(vm_xml.VMXML.new_from_dumpxml(vm_name), "disk", disk_attrs, 1)
        vm.start()
        vm_session = vm.wait_for_login()

        test.log.info("TEST_STEP: Check r/w operations on vhostvdpa disk.")
        new_disk = libvirt_disk.get_non_root_disk_name(vm_session)[0]
        utils_disk.mount(f"/dev/{new_disk}", "/mnt", session=vm_session)
        before_bc_string = utils_misc.generate_random_string(8)
        cmd_in_vm = "echo {0} >> /mnt/test ;sync; grep {0} /mnt/test".format(before_bc_string)
        vm_session.cmd(cmd_in_vm)

        test.log.info("TEST_STEP: Blockcopy the vhostvdpa disk to a new vhostdisk location.")
        bc_xml_obj = libvirt_vmxml.create_vm_device_by_type("disk", bc_disk_attrs)
        bc_options = params.get("bc_options").format(bc_xml_obj.xml)
        virsh.blockcopy(vm_name, disk_target, "", options=bc_options, **VIRSH_ARGS)

        test.log.info("TEST_STEP: Check VM's xml.")
        vdpa_device = vm_xml.VMXML.new_from_dumpxml(vm.name)\
            .devices.by_device_tag("disk")[-1]
        vdpa_dev_attrs = vdpa_device.fetch_attrs()
        test.log.debug(vdpa_dev_attrs)
        vdpa_dev_source = vdpa_dev_attrs["source"]["attrs"].get("dev")
        exp_dev_source = bc_disk_attrs["source"]["attrs"].get("dev")
        if vdpa_dev_source != exp_dev_source:
            test.fail("Incorrect disk dev after blockcopy! Expected: '%s', "
                      "Actual: '%s'." % (exp_dev_source, vdpa_dev_source))

        test.log.info("TEST_STEP: Check r/w operations on vhostvdpa disk after blockcopy")
        vm_session = vm.wait_for_login()
        utils_disk.mount(f"/dev/{new_disk}", "/mnt", session=vm_session)
        cmd_in_vm = "grep {0} /mnt/test".format(before_bc_string)
        vm_session.cmd(cmd_in_vm)
        after_bc_string = utils_misc.generate_random_string(8)
        cmd_in_vm = "echo {0} >> /mnt/test ;sync; grep {0} /mnt/test".format(after_bc_string)
        vm_session.cmd(cmd_in_vm)

    finally:
        bkxml.sync()
        test_env_obj.cleanup()
