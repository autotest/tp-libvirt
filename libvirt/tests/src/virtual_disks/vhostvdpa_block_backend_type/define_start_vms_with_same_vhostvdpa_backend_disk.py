from virttest import libvirt_version
from virttest import utils_vdpa
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

VIRSH_ARGS = {'debug': True, 'ignore_status': False}


def run(test, params, env):
    """
    Verify that if two vms use the same vhost-vdpa backend, the second one will
    fail to start
    """
    libvirt_version.is_libvirt_feature_supported(params)

    disk_attrs = eval(params.get("disk_attrs", "{}"))
    err_msg = params.get("err_msg")
    vms = params.get('vms').split()
    vm_list = [env.get_vm(v_name) for v_name in vms]
    vm, vm2 = vm_list[:2]

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm.name)
    vm2_xml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm2.name)

    bkxml = vmxml.copy()

    try:
        test.log.info("TEST_STEP: Define two VMs with the same vhostvdpa disk.")
        test_env_obj = utils_vdpa.VDPASimulatorTest(
            sim_dev_module="vdpa_sim_blk", mgmtdev="vdpasim_blk")
        test_env_obj.setup()
        for _vm in [vm, vm2]:
            vm_xml.VMXML.set_memoryBacking_tag(
                _vm.name, access_mode="shared", hpgs=False)
            libvirt_vmxml.modify_vm_device(
                vm_xml.VMXML.new_from_dumpxml(_vm.name), "disk", disk_attrs, 1)
            test.log.debug(f'VMXML of {_vm.name}:\n{virsh.dumpxml(_vm.name).stdout_text}')

        test.log.info("TEST_STEP: Start the VMs.")
        vm.start()
        cmd_result = virsh.start(vm2.name)
        libvirt.check_result(cmd_result, err_msg)
    finally:
        bkxml.sync()
        vm2_xml_backup.sync()
        test_env_obj.cleanup()
