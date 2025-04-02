import logging as log

from avocado.utils import process

from virttest import virsh
from virttest import libvirt_version
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test cpu feature
    1. Verify cpu feature policy work as expected with cpu mode=passthrough
    """
    def parse_libvirt_version(version_str):
        """Parse libvirt version string into a tuple of up to three integers."""
        parts = list(map(int, version_str.split(".")))
        return tuple(parts + [0] * (3 - len(parts)))  # Ensure three elements

    def update_cpu_xml():
        """
        Update cpu xml for test
        """
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

        # Create cpu xml for test
        if vmxml.xmltreefile.find('cpu'):
            cpu_xml = vmxml.cpu
        else:
            cpu_xml = vm_xml.VMCPUXML()
        if cpu_mode:
            cpu_xml.mode = cpu_mode
        if cpu_check:
            cpu_xml.check = cpu_check
        # Remove features before setting it
        for idx in range(len(cpu_xml.get_feature_list())-1, -1, -1):
            cpu_xml.remove_feature(idx)

        cpu_xml.add_feature(feature_name, policy=feature_policy)

        # Update vm's cpu
        vmxml.cpu = cpu_xml
        vmxml.sync()

    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)

    cpu_mode = params.get('cpu_mode', "host-passthrough")
    cpu_check = params.get('cpu_check', "full")
    feature_name = params.get('feature_name', "vmx")
    feature_check_name = params.get('feature_check_name', feature_name)
    feature_policy = params.get('feature_policy', "require")
    host_supported_feature = "yes" == params.get("host_supported_feature", "no")
    check_vm_feature = "yes" == params.get("check_vm_feature", "no")
    status_error = "yes" == params.get("status_error", "no")
    libvirt_version_par = parse_libvirt_version(params.get("libvirt_version", "6.0"))
    err_msg = params.get("err_msg")

    bkxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    try:
        if not libvirt_version.version_compare(*libvirt_version_par):
            test.cancel("Libvirt version is too low for this test.")

        flags_cmd = "grep -q %s /proc/cpuinfo" % feature_check_name
        res = process.run(flags_cmd, shell=True, ignore_status=True).exit_status
        if host_supported_feature == bool(res):
            test.cancel('The host does not support current test!')

        update_cpu_xml()

        logging.debug("The VM XML with cpu feature: \n%s",
                      virsh.dumpxml(vm_name).stdout_text.strip())
        result = virsh.start(vm_name, debug=True, ignore_status=True)
        libvirt.check_exit_status(result, status_error)
        if err_msg:
            libvirt.check_result(result, err_msg)

        vm_session = None
        if not status_error and check_vm_feature:
            vm_session = vm.wait_for_login()
            res = vm_session.cmd_status(flags_cmd)
            # TODO: Add checks for other feature policies
            if feature_policy == "disable":
                if not res:
                    test.fail("The vm unexpectedly contains %s flag" % feature_name)

        check_qemu_feature = params.get('check_qemu_feature', "no")

        if not status_error and check_qemu_feature:
            check_qemu_pattern = params.get('check_qemu_pattern', "")
            logging.debug(f"TEST_STEP: checking pattern in qemu-kvm command:{check_qemu_pattern}.")
            if not check_qemu_pattern:
                logging.warn("qemu_cmd_pattern is expected")
            else:
                qemu_cmd_result = libvirt.check_qemu_cmd_line(check_qemu_pattern)
                if not qemu_cmd_result:
                    test.fail(f"expected pattern ({check_qemu_pattern}) in qemu-kvm not found")

    finally:
        if vm.is_alive():
            vm.destroy(gracefully=False)
        bkxml.sync()
