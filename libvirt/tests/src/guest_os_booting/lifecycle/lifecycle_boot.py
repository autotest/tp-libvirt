import os

from virttest import data_dir
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt

from provider.guest_os_booting import guest_os_booting_base

VIRSH_ARGS = {'ignore_status': False, 'debug': True}


def run(test, params, env):
    """
    Test vm lifecycle with boot dev/order
    """
    def update_vm_xml(vmxml, params):
        """
        Update VM xml

        :param vm: VM object
        :param params: Dictionary with the test parameters
        """
        iface_dict = eval(params.get("iface_dict", "{}"))
        os_attrs_boots = eval(params.get('os_attrs_boots', '[]'))
        if os_attrs_boots:
            os_attrs = {'boots': os_attrs_boots}
            vmxml.setup_attrs(os=os_attrs)
        else:
            vm_os = vmxml.os
            vm_os.del_boots()
            vmxml.os = vm_os
            xml_devices = vmxml.devices
            if iface_dict:
                iface_obj = xml_devices.by_device_tag('interface')[0]
                iface_obj.setup_attrs(**iface_dict)
            vmxml.devices = xml_devices
            disk_attrs = xml_devices.by_device_tag('disk')[0].fetch_attrs()
            vmxml.set_boot_order_by_target_dev(
                disk_attrs['target']['dev'], params.get('disk_boot_idx'))
        vmxml.xmltreefile.write()
        test.log.debug(f"vmxml after updating: {vmxml}")
        vmxml.sync()

    vm_name = guest_os_booting_base.get_vm(params)

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm.name)
    bkxml = vmxml.copy()

    try:
        update_vm_xml(vmxml, params)
        test.log.info("TEST_STEP: Start the guest.")
        vm.start()
        vm.wait_for_login().close()

        test.log.info("TEST_STEP: Save and restore the guest.")
        save_path = os.path.join(data_dir.get_tmp_dir(), vm.name + '.save')
        virsh.save(vm.name, save_path, **VIRSH_ARGS)
        virsh.restore(save_path, **VIRSH_ARGS)
        if not libvirt.check_vm_state(vm.name, "running"):
            test.fail("VM should be running!")

        test.log.info("TEST_STEP: Managedsave the guest.")
        virsh.managedsave(vm.name, **VIRSH_ARGS)

        test.log.info("TEST_STEP: Start and destroy the guest.")
        virsh.start(vm.name, **VIRSH_ARGS)
        virsh.destroy(vm.name, **VIRSH_ARGS)
        bkxml.sync()

    finally:
        virsh.managedsave_remove(vm.name, debug=True)
        bkxml.sync()
