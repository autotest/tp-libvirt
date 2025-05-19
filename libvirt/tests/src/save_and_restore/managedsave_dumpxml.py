import os
import logging

from avocado.utils import process

from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_vmxml

LOG = logging.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test command: virsh managedsave-dumpxml
    Extract  the  domain XML that was in effect at the time the saved state file was created
    with the managedsave command.  Using --security-info will also include security sensitive information.
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    managed_save_file = "/var/lib/libvirt/qemu/save/%s.save" % vm_name
    graphic_attrs = eval(params.get('graphic_attrs', '{}'))

    def setup():
        """"
        Prepare the pre-conditions:
        1) Prepare the vm with security info;
        2) Managedsave the vm;
        """
        vmxml_ = vm_xml.VMXML.new_from_dumpxml(vm_name)
        libvirt_vmxml.modify_vm_device(vmxml_, 'graphics', graphic_attrs)
        vmxml_.sync()
        if not vm.is_alive():
            vm.start()
        LOG.info("vm %s state is %s", vm_name, virsh.domstate(vm_name).stdout)
        virsh.dumpxml(vm_name, "--xpath //graphics")
        virsh.managedsave(vm_name, ignore_status=False, debug=True)

    def check_outputxml(xml_text, graphic_setting):
        """
        Check the xml_snip
        1) check the root element, if there is no "--xpath", the root element should be <domain>;
        2) check if the xml includes the security info;
        """
        if option == "--xpath //os":
            wrap = xml_text.startswith("<os") and xml_text.endswith("</os>")
        elif option == "--xpath //os --wrap":
            wrap1 = xml_text.startswith("<nodes>") and xml_text.endswith("</nodes>")
            inner_text = xml_text[len("<nodes>"): -len("</nodes>")].strip()
            wrap2 = inner_text.startswith("<os") and inner_text.endswith("</os>")
            wrap = wrap1 and wrap2
        else:
            wrap = xml_text.startswith("<domain") and xml_text.endswith("</domain>")
        if not wrap:
            test.fail("The root elment of the xml is not expected!")
        if option == "--security-info":
            security_info = 'passwd' + "=" + "'" + graphic_setting['passwd'] + "'"
            LOG.info("Check if %s is in the xml", security_info)
            if security_info not in xml_text:
                test.fail("Can not find the 'passwd' in the xml!")
        else:
            if "passwd" in xml_text:
                test.fail("There should not be security info in the xml!")

    option = params.get("option", "")
    err_msg = params.get("err_msg", None)
    opt_r = "yes" == params.get("readonly", "no")
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bk_xml = vmxml.copy()

    try:
        LOG.info("TEST_STEP1: Managedsave the VM:")
        setup()
        LOG.info("TEST_STEP2: Dump the VM's xml by managedave-dumpxml with %s:", option)
        res = virsh.managedsave_dumpxml(vm_name, option, readonly=opt_r)
        libvirt.check_result(res, expected_fails=err_msg)
        if not err_msg:
            LOG.info("TEST_STEP3: Check the format of the xml dumped:")
            check_outputxml(res.stdout.strip(), graphic_attrs)
        virsh.destroy(vm_name)
    finally:
        virsh.managedsave_remove(vm_name, debug=True)
        if os.path.exists(managed_save_file):
            process.run("rm -f %s" % managed_save_file, shell=True, ignore_status=True)
        bk_xml.sync()
