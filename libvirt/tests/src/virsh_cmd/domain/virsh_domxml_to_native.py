import logging as log
import os

from virttest import libvirt_version
from virttest import utils_libvirtd
from virttest import virsh


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test command: virsh domxml-to-native.

    Convert domain XML config to a native guest configuration format.
    1.Prepare test environment.
    2.When the libvirtd == "off", stop the libvirtd service.
    3.Perform virsh domxml-from-native operation.
    4.Recover test environment.
    5.Confirm the test result.
    """

    # prepare
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    if not vm.is_dead():
        vm.destroy()
    vm.start()
    if not vm.is_alive():
        test.fail("VM start failed")

    domid = vm.get_id()
    domuuid = vm.get_uuid()

    dtn_format = params.get("dtn_format")
    file_xml = params.get("dtn_file_xml", "")
    extra_param = params.get("dtn_extra_param")
    extra = params.get("dtn_extra", "")
    libvirtd = params.get("libvirtd")
    status_error = params.get("status_error", "no")
    vm_id = params.get("dtn_vm_id", "")
    readonly = ("yes" == params.get("readonly", "no"))

    # For positive_test
    if status_error == "no":
        if vm_id == "id":
            vm_id = domid
        elif vm_id == "uuid":
            vm_id = domuuid
        elif vm_id == "name":
            vm_id = "%s %s" % (vm_name, extra)
        if file_xml == "":
            extra_param = extra_param + vm_id

    virsh.dumpxml(vm_name, extra="", to_file=file_xml)
    if libvirtd == "off":
        utils_libvirtd.libvirtd_stop()

    # run test case
    ret = virsh.domxml_to_native(dtn_format, file_xml, extra_param, readonly=readonly,
                                 ignore_status=True, debug=True)
    status = ret.exit_status
    conv_arg = ret.stdout.strip()

    # recover libvirtd service start
    if libvirtd == "off":
        utils_libvirtd.libvirtd_start()

    # clean up
    if os.path.exists(file_xml):
        os.remove(file_xml)

    # check status_error
    if status_error == "yes":
        if status == 0:
            if libvirtd == "off" and libvirt_version.version_compare(5, 6, 0):
                logging.info("From libvirt version 5.6.0 libvirtd is restarted "
                             "and command should succeed")
            else:
                test.fail("Run successfully with wrong command!")
    elif status_error == "no":
        if status != 0:
            test.fail("Run failed with right command")
