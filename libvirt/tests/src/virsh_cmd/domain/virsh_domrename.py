import logging
import os

from avocado.utils import path as utils_path
from avocado.utils import process

from virttest.utils_test import libvirt
from virttest import libvirt_vm
from virttest import virsh
from virttest import libvirt_xml
from virttest import utils_libguestfs
from virttest import utils_package
from virttest import utils_libvirtd
from virttest import libvirt_version


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

    # rename a guest with snapshot is not supported before libvirt-6.10.0
    if pre_vm_state == "with_snapshot":
        if not libvirt_version.version_compare(6, 10, 0):
            status_error = True

    # Replace the varaiables
    if vm_ref == "name":
        vm_ref = vm_name
    elif vm_ref == "uuid":
        vm_ref = domuuid

    if new_name == "vm2_name":
        vm2_name = ("%s" % vm_name[:-1])+"2"
        new_name = vm2_name
    elif new_name == "vm_dot":
        vm2_name = vm_name + "."
        new_name = vm2_name

    # Build input params
    dom_param = ' '.join([domain_option, vm_ref])
    new_name_param = ' '.join([new_name_option, new_name])

    if vm.is_alive():
        vm.destroy()

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
        ret_clone = utils_libguestfs.virt_clone_cmd(vm_name, vm2_name,
                                                    True, timeout=360)
        if ret_clone.exit_status:
            test.fail("Error occured when clone a second vm!")
        vm2 = libvirt_vm.VM(vm2_name, vm.params, vm.root_dir, vm.address_cache)
        virsh.dom_list("--name --all", debug=True)

    # Create object instance for renamed domain
    new_vm = libvirt_vm.VM(new_name, vm.params, vm.root_dir, vm.address_cache)

    # Prepare vm state
    if pre_vm_state not in ["shutoff", "autostart"]:
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
        if pre_vm_state == "autostart":
            virsh.autostart(dom_param, "", debug=True)
            virsh.dom_list("--all --autostart", debug=True)
            logging.debug("files under '/etc/libvirt/qemu/autostart/' are %s",
                          os.listdir('/etc/libvirt/qemu/autostart/'))

        result = virsh.domrename(dom_param, new_name_param, ignore_status=True, debug=True)

        # Raise unexpected pass or fail
        libvirt.check_exit_status(result, status_error)

        # Return expected failure for negative tests
        if status_error:
            logging.debug("Expected failure: %s", result.stderr)
            return

        # Checkpoints after domrename succeed
        else:
            list_ret = virsh.dom_list("--name --all", debug=True).stdout.strip().splitlines()
            domname_ret = virsh.domname(domuuid, debug=True).stdout.strip()
            if new_name not in list_ret or vm_name in list_ret:
                test.fail("New name does not affect in virsh list")
            if domname_ret != new_name:
                test.fail("New domain name does not affect in virsh domname uuid")

            if pre_vm_state != "autostart":
                # Try to start vm with the new name
                new_vm.start()
            else:
                # rhbz#1755303
                if libvirt_version.version_compare(5, 6, 0):
                    os.remove("/run/libvirt/qemu/autostarted")
                utils_libvirtd.libvirtd_restart()
                list_autostart = virsh.dom_list("--autostart", debug=True).stdout
                logging.debug("files under '/etc/libvirt/qemu/autostart/' are %s",
                              os.listdir('/etc/libvirt/qemu/autostart/'))
                process.run("file /etc/libvirt/qemu/autostart/%s.xml" % vm_name, verbose=True)
                if new_name not in list_autostart:
                    test.fail("Domain isn't autostarted after restart libvirtd,"
                              "or becomes a never 'autostart' one.")

    finally:
        # Remove additional vms
        if add_vm and vm2.exists() and result.exit_status:
            virsh.remove_domain(vm2_name, "--remove-all-storage")

        # Undefine newly renamed domain
        if new_vm.exists():
            if new_vm.is_alive():
                new_vm.destroy(gracefully=False)
            if pre_vm_state == "with_snapshot":
                libvirt.clean_up_snapshots(new_name)
            new_vm.undefine()

        # Recover domain state
        if pre_vm_state != "shutoff":
            if pre_vm_state == "with_snapshot" and status_error:
                libvirt.clean_up_snapshots(vm_name)
            else:
                if pre_vm_state == "managed_saved":
                    vm.start()
                vm.destroy(gracefully=False)

        # Restore VM
        vmxml_backup.sync()
