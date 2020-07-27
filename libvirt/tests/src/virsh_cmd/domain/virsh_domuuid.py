import logging

from virttest import virsh
from virttest import libvirt_xml
from virttest import utils_libvirtd
from virttest import libvirt_version

from virttest.utils_test.libvirt import check_domuuid_compliant_with_rfc4122


def run(test, params, env):
    """
    Test command: virsh domuuid.
    """
    vm_name = params.get("main_vm", "vm1")
    vm = env.get_vm(vm_name)
    vm.verify_alive()

    # Get parameters
    vm_ref = params.get("domuuid_vm_ref", "domname")
    vm_state = params.get("domuuid_vm_state", "running")
    addition_arg = params.get("domuuid_addition_arg")
    libvirtd = params.get("libvirtd", "on")
    status_error = params.get("status_error", "no")

    domid = vm.get_id()
    vmxml = libvirt_xml.VMXML.new_from_dumpxml(vm_name)
    xml_domuuid = vmxml.uuid
    logging.debug("UUID in XML is:\n%s", xml_domuuid)

    if vm_state == "shutoff":
        vm.destroy()

    # Prepare options
    if vm_ref == "domid":
        vm_ref = domid
    elif vm_ref == "hex_id":
        vm_ref = hex(int(domid))
    elif vm_ref == "domname":
        vm_ref = vm_name

    # Add additional argument
    if vm_ref and addition_arg:
        vm_ref = "%s %s" % (vm_ref, addition_arg)

    # Prepare libvirtd state
    if libvirtd == "off":
        utils_libvirtd.libvirtd_stop()

    result = virsh.domuuid(vm_ref)
    logging.debug(result)
    status = result.exit_status
    output = result.stdout.strip()

    # Recover libvirtd service start
    if libvirtd == "off":
        utils_libvirtd.libvirtd_start()

    # Check status_error
    if status_error == "yes":
        if status == 0:
            if libvirtd == "off" and libvirt_version.version_compare(5, 6, 0):
                logging.info("From libvirt version 5.6.0 libvirtd is restarted "
                             "and command should succeed")
            else:
                test.fail("Run successfully with wrong command!")
    elif status_error == "no":
        if not check_domuuid_compliant_with_rfc4122(output):
            test.fail("UUID is not compliant with RFC4122 format")
        if status != 0:
            test.fail("Run failed with right command.")
        elif xml_domuuid != output:
            test.fail("UUID from virsh command is not expected.")
