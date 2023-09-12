from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml import xcepts

from provider.guest_os_booting import guest_os_booting_base


def run(test, params, env):
    """
    Test boot order settings - negative
    """
    vm_name = guest_os_booting_base.get_vm(params)
    iface_dict = eval(params.get("iface_dict", "{}"))
    err_msg = params.get('err_msg', "Invalid value")

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm.name)
    bkxml = vmxml.copy()

    try:
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
            disk_attrs['target']['dev'], params.get('boot_index'))
        vmxml.xmltreefile.write()
        test.log.debug(f"vmxml after updating: {vmxml}")
        try:
            vmxml.sync()
        except xcepts.LibvirtXMLError as xml_error:
            test.log.debug("Failed define vm as expected: %s.", str(xml_error))
            if err_msg not in str(xml_error):
                test.fail("Unable to get expected error: %s!" % xml_error)
        else:
            test.fail("It should Fail")

    finally:
        bkxml.sync()
