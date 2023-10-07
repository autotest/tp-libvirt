from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt

from provider.guest_os_booting import guest_os_booting_base


def run(test, params, env):
    """
    Update disk device with boot order
    """
    vm_name = guest_os_booting_base.get_vm(params)
    boot_index = params.get("boot_index")
    err_msg = params.get('err_msg')

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm.name)
    bkxml = vmxml.copy()

    try:
        test.log.info("TEST_STEP: Prepare a running guest with boot order "
                      "element in disk xml.")
        vm_os = vmxml.os
        vm_os.del_boots()
        vmxml.os = vm_os
        disk_obj = vmxml.devices.by_device_tag('disk')[0]
        disk_attrs = disk_obj.fetch_attrs()
        vmxml.set_boot_order_by_target_dev(
            disk_attrs['target']['dev'], params.get('boot_index'))
        vmxml.xmltreefile.write()
        test.log.debug(f"vmxml after updating: {vmxml}")
        vmxml.sync()
        vm.start()
        vm.wait_for_login().close()

        test.log.info("TEST_STEP: Update device.")
        disk_obj.boot = int(boot_index) + 1
        res = virsh.update_device(vm.name, disk_obj.xml, debug=True)
        libvirt.check_result(res, err_msg)
    finally:
        test.log.info("TEST_TEARDOWN: Recover test environment.")
        bkxml.sync()
