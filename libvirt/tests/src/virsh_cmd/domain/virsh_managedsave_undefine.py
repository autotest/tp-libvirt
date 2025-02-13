import os
import logging
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt

VIRSH_ARGS = {'debug': True, 'ignore_status': False}
LOG = logging.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test option: --managed-save

    Undefine a vm with or without --managed-save option
    1. start a vm, and do managedsave
    2. try to undefine the vm without "--managed-save" option, it should fail
    3. try to undefine the vm with "--managed-save" option, it succeeds
    """

    def vm_undefine_check(vm_name):
        """
        Check if vm can be undefined with managed-save option
        """
        if not os.path.exists(managed_save_file):
            test.fail("Can't find managed save image")
        LOG.info("Step2: Undefine the VM without --managed-save option:")
        ret = virsh.undefine(vm_name, options='--nvram', ignore_status=True, debug=True)
        libvirt.check_exit_status(ret, expect_error=True)
        LOG.info("Step3: Undefine the VM with --managed-save option:")
        ret1 = virsh.undefine(vm_name, options="--managed-save --nvram",
                              ignore_status=True)
        LOG.debug("%s", ret1)
        if ret1.exit_status:
            test.fail("Guest can't be undefined with "
                      "managed-save option!")

        if os.path.exists(managed_save_file):
            test.fail("Managed save image exists after undefining vm!")
        # restore and start the vm
        bk_xml.define()
        vm.start()

    vm_name = params.get('main_vm')
    managed_save_file = "/var/lib/libvirt/qemu/save/%s.save" % vm_name
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bk_xml = vmxml.copy()
    try:
        vm = env.get_vm(vm_name)
        LOG.info("Step1: start the VM and do managedsave:")
        if not vm.is_alive:
            vm.start()
        virsh.managedsave(vm_name, **VIRSH_ARGS)
        vm_undefine_check(vm_name)
    finally:
        bk_xml.sync()
