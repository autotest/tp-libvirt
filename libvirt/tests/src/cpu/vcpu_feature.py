import logging

from avocado.utils import process

from virttest import virsh
from virttest import libvirt_version
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Test cpu feature
    1. Verify cpu feature policy work as expected with cpu mode=passthrough
    """
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
    feature_policy = params.get('feature_policy', "require")
    host_supported_feature = "yes" == params.get("host_supported_feature", "no")
    check_vm_feature = "yes" == params.get("check_vm_feature", "no")
    status_error = "yes" == params.get("status_error", "no")
    err_msg = params.get("err_msg")

    bkxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    try:
        if not libvirt_version.version_compare(6, 0, 0):
            test.cancel("Libvirt version is too low for this test.")

        flags_cmd = "grep -q %s /proc/cpuinfo" % feature_name
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

        if not status_error and check_vm_feature:
            vm_session = vm.wait_for_login()
            res = vm_session.cmd_status(flags_cmd)
            # TODO: Add checks for other feature policies
            if feature_policy == "disable":
                if not res:
                    test.fail("The vm unexpectedly contains %s flag"
                              % feature_name)

    finally:
        if vm.is_alive():
            vm.destroy(gracefully=False)
        bkxml.sync()
