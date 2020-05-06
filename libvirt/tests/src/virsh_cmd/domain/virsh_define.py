import logging

from virttest import virt_vm
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml import LibvirtXMLError
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Test defining/undefining domain by dumping xml, changing it, and re-adding.

    (1) Get name and uuid of existing vm
    (2) Rename domain and verify domain start
    (3) Get uuid of new vm
    (4) Change name&uuid back to original
    (5) Verify domain start
    """

    def do_rename(vm, new_name, uuid=None, fail_info=[]):
        # Change name in XML
        logging.info("Rename %s to %s.", vm.name, new_name)
        try:
            vm = vm_xml.VMXML.vm_rename(vm, new_name, uuid)  # give it a new uuid
        except LibvirtXMLError as detail:
            test.fail("Rename %s to %s failed:\n%s"
                      % (vm.name, new_name, detail))

        # Exercize the defined XML
        try:
            vm.start()
        except virt_vm.VMStartError as detail:
            # Do not raise TestFail because vm should be restored
            fail_info.append("Start guest %s failed:%s" % (vm.name, detail))
        vm.destroy()
        return fail_info

    def prepare_xml_file():
        """
        prepare an invalid xml with video device containing
        invalid attribute "foot", for example,
        <model type='qxl' ram='65536' vram='65536' vgamem='65536' foot='3'/>
        """
        new_xml = xml_backup.copy()
        video_device_xml = new_xml.xmltreefile.find('/devices/video/model')
        video_device_xml.set("foot", "3")
        logging.debug("The new xml used for defining a new domain is %s", new_xml)
        xml_file = new_xml.xmltreefile.name

        # undefine the original vm
        virsh.undefine(vm_name)
        return xml_file

    # Run test
    vm_name = params.get("main_vm")
    vm = env.get_vm(params["main_vm"])
    new_name = params.get("new_name", "test")
    status_error = params.get("status_error") == "yes"
    readonly = params.get("readonly") == "yes"
    option_ref = params.get("option_ref")
    is_defined_from_xml = params.get("is_defined_from_xml") == "yes"
    error_msg = params.get("error_msg")
    uuid = vm.get_uuid()
    logging.info("Original uuid: %s", vm.get_uuid())

    xml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    xml_backup_file = xml_backup.xmltreefile.name

    virsh_dargs = {"debug": True}
    if readonly:
        virsh_dargs["readonly"] = True

    expected_fails_msg = []
    if error_msg:
        expected_fails_msg.append(error_msg)

    if is_defined_from_xml:
        xml_file = prepare_xml_file()
        try:
            ret = virsh.define(xml_file, option_ref, **virsh_dargs)
            libvirt.check_result(ret, expected_fails=expected_fails_msg)
        finally:
            # for positive test, undefine the defined vm firstly
            if not ret.exit_status:
                virsh.undefine(vm_name)
            # restore the orignal vm
            virsh.define(xml_backup_file)
        return

    assert uuid is not None
    # Rename to a new name
    fail_info = do_rename(vm, new_name)
    logging.info("Generated uuid: %s", vm.get_uuid())

    # Rename back to original to maintain known-state
    fail_info = do_rename(vm, vm_name, uuid, fail_info)
    logging.info("Final uuid: %s", vm.get_uuid())

    if len(fail_info):
        test.fail(fail_info)
