import logging

from virttest.utils_test import libvirt
from virttest import libvirt_vm
from virttest import virsh
from virttest import libvirt_xml


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
    vm_state = params.get("domrename_vm_state", "shutoff")
    domain_option = params.get("dom_opt", "")
    new_name_option = params.get("newname_opt", "")

    # Create object instance for renamed domain
    new_vm = libvirt_vm.VM(new_name, vm.params, vm.root_dir, vm.address_cache)

    # Replace the varaiables
    if vm_ref == "name":
        vm_ref = vm_name
    else:
        vm_ref = domuuid

    # Build input params
    dom_param = ' '.join([domain_option, vm_ref])
    new_name_param = ' '.join([new_name_option, new_name])

    # Backup for recovery.
    vmxml_backup = libvirt_xml.vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

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
            list_ret = virsh.dom_list("--name --all", debug=True).stdout.strip()
            domname_ret = virsh.domname(domuuid, debug=True).stdout.strip()
            if list_ret != new_name:
                test.fail("New name does not affect in virsh list")
            if domname_ret != new_name:
                test.fail("New domain name does not affect in virsh domname uuid")

            # Try to start vm with the new name
            new_vm.start()

    finally:
        # Restore VM
        if new_vm.exists():
            if new_vm.is_alive():
                new_vm.destroy(gracefully=False)
            new_vm.undefine()
        vmxml_backup.sync()
