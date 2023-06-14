from virttest import virsh

from virttest.libvirt_xml.vm_xml import VMXML
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Test security confined options in qemu.conf.
    """

    status_error = 'yes' == params.get("status_error", 'no')

    # Get variables about VM and get a VM object and VMXML instance.
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vmxml = VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()

    seclabel_attr = {k.replace('seclabel_attr_', ''): int(v) if v.isdigit()
                     else v for k, v in params.items()
                     if k.startswith('seclabel_attr_')}
    qemu_conf = {k.replace('qemu_conf_', ''): v for k, v in params.items()
                 if k.startswith('qemu_conf_')}
    qemu_conf_obj = None
    try:
        test.log.info("TEST_STEP: Enable qemu namespaces in qemu.conf.")
        qemu_conf_obj = libvirt.customize_libvirt_config(qemu_conf, "qemu")

        if seclabel_attr:
            test.log.info("TEST_STEP: Update VM XML with %s.", seclabel_attr)
            vmxml.set_seclabel([seclabel_attr])
            vmxml.sync()
            test.log.debug(f"vmxml: {vmxml}")

        test.log.info("TEST_STEP: Start the VM.")
        res = virsh.start(vm.name, debug=True)
        libvirt.check_exit_status(res, status_error)

    finally:
        test.log.info("TEST_TEARDOWN: Recover test environment.")
        backup_xml.sync()

        if qemu_conf_obj:
            libvirt.customize_libvirt_config(
                None, "qemu", config_object=qemu_conf_obj,
                is_recover=True)
