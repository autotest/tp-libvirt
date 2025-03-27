import os
import logging
from virttest import virsh
from virttest import libvirt_vm
from virttest.libvirt_xml import vm_xml
from avocado.utils import process

from provider.save import save_base

LOG = logging.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Try managedsave on VMs with special name

    1. start a vm, and do managedsave
    2. check the saved file exists
    3. start the vm, check the vm status and saved file status
    """
    special_name = params.get('vmname')
    special_name_display = params.get('name_display')
    managed_save_file = "/var/lib/libvirt/qemu/save/%s.save" % special_name
    vm_name = params.get('main_vm')
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bk_xml = vmxml.copy()
    vm = env.get_vm(vm_name)
    new_vm = libvirt_vm.VM(special_name, vm.params, vm.root_dir, vm.address_cache)
    try:
        LOG.info("TEST_STEP1: Rename the VM to be with special name:")
        LOG.info("The special name is %s with %s characters.",
                 special_name, len(special_name))
        if vm.is_alive:
            vm.destroy()
        virsh.domrename(vm_name, special_name, debug=True)
        vmlist = virsh.dom_list("--name --all",
                                debug=True).stdout_text
        if special_name not in vmlist:
            test.fail("Rename the VM fail!")

        LOG.info("TEST_STEP2: Start the VM and do managedsave:")
        virsh.start(special_name, debug=True)
        pid_ping, upsince = save_base.pre_save_setup(new_vm)
        if virsh.managedsave(special_name, debug=True).exit_status:
            test.fail("VM managedsave fail!")

        LOG.info("TEST_STEP3: Ensure vm state include 'saved', and the saved file exists:")
        vm_state = virsh.domstate(special_name, extra="--reason", debug=True).stdout
        LOG.info("vm %s state is %s", special_name, vm_state)
        if "saved" not in vm_state:
            test.fail("The domain state should include 'saved'!")
        if not os.path.exists(managed_save_file):
            test.fail("The saved file does not exist: %s" % managed_save_file)

        LOG.info("TEST_STEP4: Start the VM to restore:")
        virsh.start(special_name, debug=True)
        save_base.post_save_check(new_vm, pid_ping, upsince)
        if os.path.exists(managed_save_file):
            test.fail("The saved file should be removed!")
    finally:
        virsh.destroy(special_name, debug=True)
        virsh.undefine(special_name, "--nvram --managed-save", debug=True)
        if os.path.exists(managed_save_file):
            process.run("rm -f %s" % managed_save_file, shell=True, ignore_status=True)
        bk_xml.sync()
