import logging

from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

LOG = logging.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test the command 'virsh managedsave-define'.

    This function tests the following:
    1. Retrieve the VM's XML after a managed save using 'managedsave-dumpxml'.
    2. Modify the 'on-crash' property in the retrieved XML.
    3. Run 'managedsave-define' with the modified XML.
    4. Start the vm, and verify the VM's state is expected: 'running' or 'paused').
    5. Confirm the modified XML is applied correctly by checking the live VM's XML.
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    def setup():
        """
        Prepare the VM's state and managed save XML:
        1. Ensure original value of the property 'on_crash' in the VM's XML.
        2. Managed save the VM when it is running or paused.
        """
        new_xml = libvirt_vmxml.set_vm_attrs(vmxml, original_xml)
        LOG.info("Updated XML:\n%s", new_xml)

        if not vm.is_alive():
            vm.start()

        if pause_vm:
            virsh.suspend(vm_name, ignore_status=False, debug=True)
        LOG.info("VM '%s' state is %s", vm_name, virsh.domstate(vm_name).stdout.strip())
        virsh.managedsave(vm_name, ignore_status=False, debug=True)
        LOG.info("Managed save completed for VM '%s'", vm_name)
        dom_state = virsh.domstate(vm_name, "--reason", debug=True).stdout.strip()
        if 'saved' not in dom_state:
            test.fail(f"VM '{vm_name}' state is {dom_state}, not in managedsave state!")

    def update_managedsave_xml():
        """
        Update the domain XML that will be used in managedsave-define later:

        1. Retrieve the domain XML after a managed save.
        2. Modify the 'on-crash' property with the new value.
        """
        managedsave_xml = vm_xml.VMXML.new_from_managedsave_dumpxml(vm_name)
        managedsave_xml.on_crash = updated_xml["on_crash"]
        return managedsave_xml

    pause_vm = "yes" == params.get("pause_vm", "no")
    define_option = params.get("option", "")
    status_error = "yes" == params.get("status_error", "no")
    err_msg = params.get("err_msg", None)
    opt_r = "yes" == params.get("readonly", "no")
    original_xml = eval(params.get("original_xml"))
    updated_xml = eval(params.get("updated_xml"))

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bk_xml = vmxml.copy()

    try:
        LOG.info("TEST_STEP1: Managed save the VM...")
        setup()
        LOG.info("TEST_STEP2: Dump the VM's xml by managedave-dumpxml and update the xml...")
        managed_save_xml = update_managedsave_xml()

        LOG.info(f"TEST_STEP3: Run 'virsh managedsave-define' with the updated XML using option '{define_option}'")
        res = virsh.managedsave_define(vm_name, managed_save_xml.xml, define_option, readonly=opt_r)
        libvirt.check_result(res, expected_fails=err_msg)
        LOG.info("after managedsave-dumpxml and update, then run managedsave-define with updated xml, the new xml...")

        if not status_error:
            LOG.info("TEST_STEP4: Start VM and check vm's state...")
            virsh.start(vm_name, ignore_status=False, debug=True)

            dom_state = virsh.domstate(vm_name, debug=True).stdout.strip()
            if define_option == "--running" or (define_option == "" and pause_vm == "no"):
                if "running" not in dom_state:
                    test.fail(f"VM '{vm_name}' should be running but is {dom_state}")
            elif define_option == "--paused":
                if "paused" not in dom_state:
                    test.fail("VM %s should be in paused state ,but it's %s!" % (vm_name, dom_state))

            LOG.info("TEST_STEP5: Verify the live VM's XML matches the updated XML...")
            vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
            if vmxml.on_crash != updated_xml["on_crash"]:
                test.fail(f"The live xml {vmxml.on_crash} does not match the updated xml changes {updated_xml}!")
            else:
                LOG.info(f"found {updated_xml} in the updated xml, which is expected!")
            virsh.destroy(vm_name)
    finally:
        virsh.managedsave_remove(vm_name, debug=True)
        bk_xml.sync()
