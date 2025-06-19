import os
import logging
import xml.etree.ElementTree as ET

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
        Check the XML snippet:
        1) If no --xpath option, the root element should be <domain>;
        2) If --xpath is given, validate the root structure accordingly;
        3) If --security-info is set, ensure password info is included/excluded correctly.
        """
        try:
            # If xml_text is actual content, parse from string
            root = ET.fromstring(xml_text)

            # Step 1: Validate root
            if option == "--xpath //os":
                if root.tag != "os":
                    test.fail("Expected root tag <os> but got <%s>" % root.tag)

            elif option == "--xpath //os --wrap":
                if root.tag != "nodes":
                    test.fail("Expected root tag <nodes> for wrapped content.")
                children = list(root)
                if not children or children[0].tag != "os":
                    test.fail("Expected first child under <nodes> to be <os>.")

            else:  # Default: should be <domain>
                if root.tag != "domain":
                    test.fail("Expected root tag <domain> but got <%s>" % root.tag)

            # Step 2: Validate security info
            if option == "--security-info":
                expected = f"passwd='{graphic_setting.get('passwd', '')}'"
                LOG.info("Checking for presence of: %s", expected)
                if expected not in xml_text:
                    test.fail("Missing security info (passwd) in the XML.")
            else:
                if "passwd" in xml_text:
                    test.fail("Unexpected security info (passwd) found in the XML.")

        except ET.ParseError as e:
            test.fail(f"XML parsing failed: {e}")

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
