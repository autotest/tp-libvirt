import os
import pwd

from avocado.utils import process

from virttest import data_dir
from virttest import libvirt_version
from virttest import virsh

from virttest.libvirt_xml import xcepts
from virttest.libvirt_xml.vm_xml import VMXML
from virttest.utils_libvirt import libvirt_disk
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt


def get_dev_dict(device_type, params):
    """Get device settings

    :param device_type: device type, eg. disk
    :param params: Test Dictionary with the test parameters
    :return: Device attrs and it's seclabel attrs
    """
    seclabel_attr = {k.replace('seclabel_attr_', ''): int(v) if v.isdigit()
                     else v for k, v in params.items()
                     if k.startswith('seclabel_attr_')}

    if device_type == "disk":
        new_image_path = data_dir.get_data_dir() + '/test.img'
        libvirt_disk.create_disk("file", new_image_path, disk_format="qcow2")
        dev_dict = eval(params.get("disk_attrs", "{}"))
        dev_dict.update({'source': {'attrs': {'file': new_image_path},
                                    'seclabels': [seclabel_attr]}})
    else:
        dev_dict = eval(params.get("serial_attrs", "{}"))
        dev_dict.update({'sources': [{"attrs": eval(
            params.get("serial_attrs_sources_attrs", "{}")),
                                      'seclabels': [seclabel_attr]}]})
    return dev_dict, seclabel_attr


def run(test, params, env):
    """
    Start VM or hotplug a device with per-device dac <seclabel> setting

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment
    """
    test_device = params.get("test_device")
    test_scenario = params.get("test_scenario")
    # Get general variables.
    added_user = None
    source_path = None

    # Get variables about VM and get a VM object and VMXML instance.
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vmxml = VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()

    status_error = 'yes' == params.get("status_error", 'no')

    libvirt_version.is_libvirt_feature_supported(params)

    dev_dict, seclabel_attr = get_dev_dict(test_device, params)
    test.log.debug(dev_dict)

    try:
        if seclabel_attr.get("label") and not status_error:
            user = seclabel_attr.get("label").split(":")[0]
            if user not in [x.pw_name for x in pwd.getpwall()]:
                process.run("useradd %s" % user, shell=True, verbose=True)
                added_user = user

        if test_scenario != "hot_plug":
            test.log.info("TEST_STEP: Start VM with per-device dac setting.")
            try:
                libvirt_vmxml.modify_vm_device(vmxml, test_device, dev_dict)
            except xcepts.LibvirtXMLError as details:
                if not status_error:
                    test.fail(details)
            test.log.debug("VM XML: %s.", VMXML.new_from_inactive_dumpxml(vm_name))
            res = virsh.start(vm.name)
        else:
            test.log.info("TEST_STEP: Hot plug a device with dac setting.")
            if test_device == 'serial':
                controller_dict = {'model': 'pcie-to-pci-bridge'}
                libvirt_vmxml.modify_vm_device(vmxml, "controller", controller_dict, 50)
            vm.start()
            vm.wait_for_login().close()

            dev_obj = libvirt_vmxml.create_vm_device_by_type(test_device, dev_dict)
            dev_obj.setup_attrs(**dev_dict)
            res = virsh.attach_device(vm.name, dev_obj.xml, debug=True)
        libvirt.check_exit_status(res, status_error)

        if seclabel_attr.get("label") and not status_error:
            source_path = dev_dict['source']['attrs']['file'] \
                if test_device == "disk" else params.get(
                "serial_path", "/tmp/test1.sock")
            test.log.info("TEST_STEP: Check ownership of '%s'.", source_path)
            stat_re = os.lstat(source_path)
            test.log.debug(stat_re)
            usr = seclabel_attr.get("label").split(":")[0]
            if [stat_re.st_uid, stat_re.st_gid] != [
               pwd.getpwnam(usr).pw_uid, pwd.getpwnam(usr).pw_gid]:
                test.fail("Incorrect ownership of source file!")

    finally:
        test.log.info("TEST_TEARDOWN: Recover test environment.")
        vm.destroy(gracefully=False)
        backup_xml.sync()

        if added_user:
            process.run("userdel -r %s" % added_user,
                        ignore_status=False, shell=True)
        if source_path and os.path.exists(source_path):
            os.remove(source_path)
