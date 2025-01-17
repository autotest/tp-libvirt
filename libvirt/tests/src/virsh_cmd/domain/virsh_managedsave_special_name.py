import os
import logging
from virttest import virsh
from virttest import libvirt_vm
from virttest.libvirt_xml import vm_xml
from avocado.utils import process

from provider.save import save_base

VIRSH_ARGS = {'debug': True, 'ignore_status': True}
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
    # Create object instance for renamed domain
    new_vm = libvirt_vm.VM(special_name, vm.params, vm.root_dir, vm.address_cache)
    try:
        LOG.info("Step1: Rename the VM to be with special name:")
        LOG.info("The special name is %s with %s characters.",
                 special_name, len(special_name))
        if vm.is_alive:
            vm.destroy()
        virsh.domrename(vm_name, special_name, **VIRSH_ARGS)
        vmlist = virsh.dom_list("--name --all",
                                **VIRSH_ARGS).stdout.strip()
        if special_name not in vmlist:
            test.fail("Rename the VM fail!")

        LOG.info("Step2: Start the VM and do managedsave:")
        virsh.start(special_name, **VIRSH_ARGS)
        pid_ping, upsince = save_base.pre_save_setup(new_vm)
        if virsh.managedsave(special_name, **VIRSH_ARGS).exit_status:
            test.fail("VM managedsave fail!")

        LOG.info("Step3: Check the domain states and saved file:")
        # Ensure vm state include "saved", and the saved file exists
        dom_state = virsh.dom_list("--all --managed-save",
                                   **VIRSH_ARGS).stdout.strip().splitlines()
        vm_state = str([item for item in dom_state if special_name_display in item])
        LOG.info("vm %s state is %s", special_name, vm_state)
        if "saved" not in vm_state:
            test.fail("The domain state should includes 'saved'!")
        if not os.path.exists(managed_save_file):
            test.fail("The saved file do not exists: %s" % managed_save_file)

        LOG.info("Step4: Start the VM to restore:")
        virsh.start(special_name, **VIRSH_ARGS)
        save_base.post_save_check(new_vm, pid_ping, upsince)
        if os.path.exists(managed_save_file):
            test.fail("The saved file should be removed!")
    finally:
        virsh.destroy(special_name, **VIRSH_ARGS)
        virsh.undefine(special_name, "--nvram --managed-save", **VIRSH_ARGS)
        if os.path.exists(managed_save_file):
            process.run("rm -f %s" % managed_save_file, shell=True, ignore_status=True)
        bk_xml.sync()
