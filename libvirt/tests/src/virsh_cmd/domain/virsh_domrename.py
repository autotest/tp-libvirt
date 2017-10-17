import logging

from avocado.utils import path as utils_path

from virttest.utils_test import libvirt
from virttest import libvirt_vm
from virttest import virsh
from virttest import libvirt_xml
from virttest import utils_libguestfs
from virttest import utils_package


def run(test, params, env):
    """
    Test command: virsh rename.

    The command can rename a domain.
    1.Prepare test environment.
    2.Perform virsh rename operation.
    3.Recover test environment.
    4.Confirm the test result.
    """
    # Get specific parameter value
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    domuuid = vm.get_uuid()
    vm_ref = params.get("domrename_vm_ref", "name")
    status_error = "yes" == params.get("status_error", "no")
    new_name = params.get("vm_new_name", "new")
    pre_vm_state = params.get("domrename_vm_state", "shutoff")
    domain_option = params.get("dom_opt", "")
    new_name_option = params.get("newname_opt", "")
    add_vm = "yes" == params.get("add_vm", "no")

    # Replace the varaiables
    if vm_ref == "name":
        vm_ref = vm_name
    elif vm_ref == "uuid":
        vm_ref = domuuid

    if new_name == "vm2_name":
        vm2_name = ("%s" % vm_name[:-1])+"2"
        new_name = vm2_name

    # Build input params
    dom_param = ' '.join([domain_option, vm_ref])
    new_name_param = ' '.join([new_name_option, new_name])

    # Backup for recovery.
    vmxml_backup = libvirt_xml.vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    logging.debug("vm xml is %s", vmxml_backup)

    # Clone additional vms if needed
    if add_vm:
        try:
            utils_path.find_command("virt-clone")
        except utils_path.CmdNotFoundError:
            if not utils_package.package_install(["virt-install"]):
                test.cancel("Failed to install virt-install on host")
        utils_libguestfs.virt_clone_cmd(vm_name, vm2_name, True, timeout=360)
        vm2 = libvirt_vm.VM(vm2_name, vm.params, vm.root_dir, vm.address_cache)
        virsh.dom_list("--name --all", debug=True)

    # Create object instance for renamed domain
    new_vm = libvirt_vm.VM(new_name, vm.params, vm.root_dir, vm.address_cache)

    # Prepare vm state
    if pre_vm_state != "shutoff":
        vm.start()
        if pre_vm_state == "paused":
            vm.pause()
            logging.debug("Domain state is now: %s", vm.state())
        elif pre_vm_state == "managed_saved":
            vm.managedsave()
        elif pre_vm_state == "with_snapshot":
            virsh.snapshot_create_as(vm_name, "snap1 --disk-only", debug=True)
            vm.destroy(gracefully=False)

    try:
        result = virsh.domrename(dom_param, new_name_param, ignore_status=True, debug=True)

        # Raise unexpected pass or fail
        libvirt.check_exit_status(result, status_error)

        # Return expected failure for negative tests
        if status_error:
            logging.debug("Expected failure: %s", result.stderr)
            return

        # Checkpoints after domrename succeed
        else:
            list_ret = virsh.dom_list("--name --all", debug=True).stdout
            domname_ret = virsh.domname(domuuid, debug=True).stdout.strip()
            if new_name not in list_ret or vm_name in list_ret:
                test.fail("New name does not affect in virsh list")
            if domname_ret != new_name:
                test.fail("New domain name does not affect in virsh domname uuid")

            # Try to start vm with the new name
            new_vm.start()

    finally:
        # Remove additional vms
        if add_vm and vm2.exists():
            virsh.remove_domain(vm2_name, "--remove-all-storage")

        # Undefine newly renamed domain
        if new_vm.exists():
            if new_vm.is_alive():
                new_vm.destroy(gracefully=False)
            new_vm.undefine()

        # Recover domain state
        if pre_vm_state != "shutoff":
            if pre_vm_state == "with_snapshot":
                libvirt.clean_up_snapshots(vm_name)
            else:
                if pre_vm_state == "managed_saved":
                    vm.start()
                vm.destroy(gracefully=False)

        # Restore VM
        vmxml_backup.sync()
