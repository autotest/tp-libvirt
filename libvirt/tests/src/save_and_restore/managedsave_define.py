import os
import logging

from avocado.utils import process

from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt
from virttest import data_dir

LOG = logging.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test command: virsh managedsave-define
    Update the domain XML that will be used when domain is later started.
    The managed save image records whether the domain should be started  to
    a  running  or paused state.
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    managed_save_file = "/var/lib/libvirt/qemu/save/%s.save" % vm_name

    def setup():
        """"
        Prepare the pre-conditions:
        1. Ensure original value of the property "on_crash" of vmxml;
        2. Managedsave vm when it is in running state or paused state;
        """
        new_xml = libvirt_vmxml.set_vm_attrs(vmxml, original_xml)
        LOG.info("Updated XML:\n%s", new_xml)
        if not vm.is_alive():
            vm.start()
        # Pause VM if requested and resume afterwards to ensure correct state
        vm_paused = params.get('vm_pause', 'no') == 'yes'
        if vm_paused:
            virsh.suspend(vm_name, ignore_status=False, debug=True)
        LOG.info("VM '%s' state is %s", vm_name, virsh.domstate(vm_name).stdout.strip())
        virsh.managedsave(vm_name, ignore_status=False, debug=True)
        LOG.info("Managedsave completed for VM '%s'", vm_name)
        # Check VM's status after setup; managedsave VM should be inactive with "saved" state.
        dom_state_ = virsh.domstate(vm_name, "--reason", debug=True).stdout.strip()
        if 'saved' not in dom_state_:
            test.fail("VM '%s' state is %s, not in managedsave state!", vm_name, dom_state_)

    def update_managedsave_xml():
        """
        Update the domain XML that will be used when domain is later started.
        1). After managedsave the VM, Get the domain xml by mangedsave_dumpxml;
        2). Alter the VM definition, update from the original_xml to updated_xml;
        """
        tmp_dir = data_dir.get_tmp_dir()
        managed_save_xml_ = os.path.join(tmp_dir, 'managed_save.xml')
        virsh.managedsave_dumpxml(vm_name, ' > %s' % managed_save_xml_, ignore_status=False, debug=True)
        LOG.info("Before update:")
        virsh.dumpxml(vm_name, "--xpath //on_crash", debug=True)
        with open(managed_save_xml_) as f:
            content = f.read()
        if original_xml_text in content:
            content = content.replace(original_xml_text, updated_xml_text)
            with open(managed_save_xml_, 'w') as f:
                f.write(content)
        else:
            test.error("The content to be updated not exists in xml!")
        LOG.info("After update:")
        process.run("grep 'on_crash' %s" % managed_save_xml_, shell=True)
        return managed_save_xml_

    vm_paused = "yes" == params.get("vm_paused", "no")
    define_option = params.get("option", "")
    status_error = "yes" == params.get("status_error", "no")
    err_msg = params.get("err_msg", None)
    opt_r = "yes" == params.get("readonly", "no")
    original_xml = eval(params.get("original_xml"))
    updated_xml = eval(params.get("updated_xml"))
    original_xml_text = f'<on_crash>{original_xml["on_crash"]}</on_crash>'
    updated_xml_text = f'<on_crash>{updated_xml["on_crash"]}</on_crash>'

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bk_xml = vmxml.copy()

    try:
        LOG.info("TEST_STEP1: Managedsave the VM:")
        setup()
        LOG.info("TEST_STEP2: Dump the VM's xml by managedave-dumpxml and update the xml:")
        managed_save_xml = update_managedsave_xml()

        LOG.info("TEST_STEP3: Run managedsave-define with this updated xml with %s:", define_option)
        res = virsh.managedsave_define(vm_name, managed_save_xml, define_option, readonly=opt_r)
        libvirt.check_result(res, expected_fails=err_msg)

        if not status_error:
            LOG.info("TEST_STEP4: Start VM and check vm's state:")
            virsh.start(vm_name, ignore_status=False, debug=True)
            dom_state = virsh.domstate(vm_name, debug=True).stdout.strip()
            if define_option == "--running" or (define_option == "" and vm_paused == "no"):
                if "running" not in dom_state:
                    test.fail("VM %s should be in running state, but it's %s!" % (vm_name, dom_state))
            elif define_option == "--paused":
                if "paused" not in dom_state:
                    test.fail("VM %s should be in paused state ,but it's %s!" % (vm_name, dom_state))

            LOG.info("TEST_STEP5: Check the live xml updated:")
            if not libvirt.check_dumpxml(vm, updated_xml_text):
                test.fail("The live xml do not includes the xml changes!")
            else:
                LOG.info(f"Found {updated_xml_text} in the vm xml!")
            virsh.destroy(vm_name)
    finally:
        virsh.managedsave_remove(vm_name, debug=True)
        if os.path.exists(managed_save_file):
            process.run("rm -f %s" % managed_save_file, shell=True, ignore_status=True)
        bk_xml.sync()
