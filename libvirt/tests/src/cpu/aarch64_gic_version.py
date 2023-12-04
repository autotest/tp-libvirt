import re
import logging

from avocado.utils import process
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


LOG = logging.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test GIC version feature

    :param test: QEMU test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)

    start_vm = params.get("start_vm", "no")
    check_gic_command_host = params.get("check_gic_command_host", "")
    check_gic_command_guest = params.get("check_gic_command_guest", "")
    gic_version = params.get("gic_version", "")

    status_error = "yes" == params.get("status_error", "no")
    err_msg = params.get("err_msg", "")

    def _check_host_support(vm):
        """
        Check the host support for GIC

        :param vm: The virtual machine
        """
        # Checks whether CPU has GIC
        if not process.run(
                check_gic_command_host,
                ignore_status=True,
                shell=True).stdout_text:
            test.cancel("Host doesn't support CPU GIC")

    def _check_vm_start(vm):
        """
        Verifies GIC version setting in VM XML works as expected.

        :param vm: The virtual machine
        :param gic_version: The GIC version value to set and verify
        """
        # Update VM XML with the specified GIC version
        # Start the VM and capture the output
        result = virsh.start(vm.name, debug=True)
        libvirt.check_exit_status(result, status_error)

    def _check_gic_version(vm, gic_version):
        """
        Check the host and guest gic version

        :param vm: The virtual machine
        :param gic_version: The GIC version value to test (eg, "2", "3", or "host")
        """
        if not vm.is_alive():
            LOG.debug("VM is not active, skipping GIC version check")
            return

        session = None
        try:
            # Check GIC version on host
            host_gic_version_output = process.run(
                check_gic_command_host, shell=True).stdout_text

            match = re.search(r"(GICv\d+)", host_gic_version_output)
            if not match:
                test.fail("Could not determine host GIC version")
            else:
                actual_host_gic_version = match.group(1)

            # Verify guest GIC version based on GIC version
            session = vm.wait_for_login(timeout=120)
            ret_guest = session.cmd_output(check_gic_command_guest)

            exp_gic_version = actual_host_gic_version if gic_version == 'host' else 'GICv%s' % gic_version
            if exp_gic_version not in ret_guest:
                test.fail(
                    f"Guest GIC version mismatch for GICv{gic_version}. Expected: {exp_gic_version}, Actual: {ret_guest}")

        finally:
            if session:
                session.close()

    # Close guest and edit guest xml
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    original_vm_xml = vmxml.copy()

    try:
        # Additional preparation steps for GIC tests can be added here
        _check_host_support(vm)

        vm_features = vmxml.features
        vm_features.add_feature('gic', 'version', gic_version)
        vmxml.features = vm_features
        LOG.debug(f"Testing GIC version: {gic_version}")

        LOG.debug("Original VM XML:\n{original_vm_xml}")

        LOG.debug("Updated VM XML:\n{vmxml}")

        vmxml.sync()

        _check_vm_start(vm)
        _check_gic_version(vm, gic_version)

    finally:
        # Restore guest
        if vm.is_alive():
            vm.destroy(gracefully=False)
        original_vm_xml.sync()
