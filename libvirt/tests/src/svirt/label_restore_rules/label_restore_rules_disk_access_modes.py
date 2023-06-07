import os

from avocado.utils import process
from virttest import data_dir
from virttest.libvirt_xml.vm_xml import VMXML
from virttest.utils_libvirt import libvirt_disk
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt


def get_seclabel_attrs(params):
    """Get seclabel settings

    :param params: Test Dictionary with the test parameters
    :return: seclabel attrs and the expected label setting
    """
    dac_seclabel_attr = {k.replace('dac_seclabel_attr_', ''): int(v)
                         if v.isdigit()
                         else v for k, v in params.items()
                         if k.startswith('dac_seclabel_attr_')}
    selinux_seclabel_attr = {k.replace('selinux_seclabel_attr_', ''): int(v)
                             if v.isdigit()
                             else v for k, v in params.items()
                             if k.startswith('selinux_seclabel_attr_')}
    expr_label = " ".join(dac_seclabel_attr['label'].split(":") +
                          [':'.join(selinux_seclabel_attr['label']
                                    .split(':')[:4])])

    return [dac_seclabel_attr, selinux_seclabel_attr], expr_label


def run(test, params, env):
    """
    Check svirt label and restore rules for different disk access modes

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    disk_attrs = eval(params.get("disk_attrs", "{}"))
    disk_index = 0
    seclabel_attrs, expr_label = get_seclabel_attrs(params)

    vmxml = VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()
    try:
        test.log.info("TEST_SETUP: Prepare a VM with the proper access mode disk.")
        disk_path = vm.get_first_disk_devices()['source']
        if disk_attrs.get("share"):
            disk_path = data_dir.get_data_dir() + '/test.img'
            libvirt_disk.create_disk("file", disk_path, disk_format="qcow2")
            disk_attrs.update({'source': {'attrs': {'file': disk_path}}})
            disk_index = 1
        org_label = process.run("ls -lZ %s | awk '{print $3,$4,$5}'"
                                % disk_path, shell=True).stdout_text.strip()
        libvirt_vmxml.modify_vm_device(vmxml, "disk", disk_attrs,
                                       index=disk_index)

        test.log.info("TEST_STEP: Update VM XML with %s.", seclabel_attrs)
        vmxml.set_seclabel(seclabel_attrs)
        vmxml.sync()
        vm.start()
        test.log.debug(VMXML.new_from_dumpxml(vm_name))

        test.log.info("TEST_STEP: Check the label of disk image after the VM "
                      "starts.")
        label_output = process.run("ls -lZ %s" % disk_path, shell=True)
        libvirt.check_result(label_output, expected_match=expr_label)

        test.log.info("TEST_STEP: Check the label of disk image after "
                      "destroying the VM.")
        vm.destroy()
        if params.get("label_restored") == "yes":
            expr_label = org_label
        test.log.debug(expr_label)
        label_output2 = process.run("ls -lZ %s" % disk_path, shell=True)
        libvirt.check_result(label_output2, expected_match=expr_label)
    finally:
        test.log.info("TEST_TEARDOWN: Recover test environment.")
        backup_xml.sync()
        if disk_attrs.get("share") and os.path.exists(disk_path):
            os.remove(disk_path)
