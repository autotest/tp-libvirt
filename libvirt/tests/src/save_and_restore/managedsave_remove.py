import os
import logging
from virttest import virsh
from avocado.utils import process
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt

LOG = logging.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test command: virsh managedsave-remove

    Remove the managedsave state file for a domain, if it exists.
    This ensures the domain will do a full boot the next  time  it
    is started.
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    managed_save_file = "/var/lib/libvirt/qemu/save/%s.save" % vm_name

    def setup():
        """"
        Prepare the pre-conditions
        1. Managedsave vm if needed
        2. Prepare an invalid saved file if needed
        3. Remove the saved file if needed
        """
        if not vm.is_alive():
            vm.start()
        if vm_managedsaved:
            virsh.managedsave(vm_name, debug=True)
        if file_state == "corrupt":
            process.run("echo > %s" % managed_save_file, shell=True, ignore_status=True)
        elif file_state == "nonexist" and vm_managedsaved:
            process.run("rm -f %s" % managed_save_file, shell=True, ignore_status=True)

    file_state = params.get("file_state")
    vm_managedsaved = "yes" == params.get("vm_managedsaved")
    expected_fails = None
    expected_match = None
    if vm_managedsaved and file_state == 'nonexist':
        expected_fails = 'No such file or directory'
    if vm_managedsaved and file_state != 'nonexist':
        expected_match = 'Removed managedsave image'
    if not vm_managedsaved:
        expected_match = 'removal skipped'

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bk_xml = vmxml.copy()

    try:
        LOG.info("Step1: Setup before the managedsave-remove and "
                 "check vm state:")
        setup()
        # check VM's status after setup, managedsave vm should
        # be inactive with "saved" states.
        dom_state = virsh.dom_list("--all --managed-save",
                                   debug=True).stdout.strip().splitlines()
        vm_state = str([item for item in dom_state if vm_name in item])
        LOG.info("vm %s state is %s", vm_name, vm_state)
        if vm_managedsaved:
            if "saved" not in vm_state:
                test.fail("The domain state should includes 'saved'!")
            else:
                LOG.info("Found expected state 'saved' in %s state", vm_name)
        else:
            if "running" not in vm_state:
                test.fail("vm should be running!")
            else:
                LOG.info("Found expected state 'running' in %s state", vm_name)
        LOG.info("Step2: Do managedsave-remove:")
        ret = virsh.managedsave_remove(vm_name, debug=True)
        LOG.debug("%s", ret)
        libvirt.check_result(ret, expected_fails=expected_fails,
                             expected_match=expected_match)
        LOG.info("Step3: Check the vm state:")
        dom_state1 = virsh.dom_list("--all --managed-save",
                                    debug=True).stdout.strip().splitlines()
        vm_state1 = str([item for item in dom_state1 if vm_name in item])
        LOG.info("vm %s state is %s", vm_name, vm_state1)
        # Normally, when vm in 'saved' state, it should be 'shut off' after removal
        if vm_managedsaved:
            if file_state == 'nonexist' and "saved" not in vm_state1:
                test.fail("The domain state should not change!")
            if file_state != 'nonexist' and "shut off" not in vm_state1:
                test.fail("The domain state should change to 'shut off'!")
        else:
            if "running" not in vm_state:
                test.fail("vm should be running!")
            else:
                LOG.info("Found expected state 'running' in %s state", vm_name)
        LOG.info("step4: Check the saved file")
        if os.path.exists(managed_save_file):
            LOG.info("The saved file exists: %s", managed_save_file)
            if vm_managedsaved or file_state != 'corrupt':
                test.fail("There should not be saved file after removal")
        else:
            LOG.info("Saved file does not exists!")
        if vm_managedsaved and file_state == 'nonexist':
            virsh.undefine(vm_name, '--managed-save --keep-nvram', debug=True)
    finally:
        virsh.managedsave_remove(vm_name, debug=True)
        if os.path.exists(managed_save_file):
            process.run("rm -f %s" % managed_save_file, shell=True, ignore_status=True)
        bk_xml.sync()
