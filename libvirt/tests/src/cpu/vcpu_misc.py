import logging

from avocado.utils import process

from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Test misc tests of virtual cpu features

    1) check dumpxml after snapshot-create/revert

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """

    def do_snapshot(vm_name, expected_str):
        """
        Run snapshot related commands: snapshot-create-as, snapshot-list
        snapshot-dumpxml, snapshot-revert

        :param vm_name: vm name
        :param expected_str: expected string in snapshot-dumpxml
        :raise: test.fail if virsh command failed
        """
        snapshot_name = vm_name + "-snap"
        virsh_dargs = {'debug': True}

        cmd_result = virsh.snapshot_create_as(vm_name, snapshot_name,
                                              **virsh_dargs)
        libvirt.check_exit_status(cmd_result)

        try:
            snapshots = virsh.snapshot_list(vm_name, **virsh_dargs)
        except process.CmdError:
            test.fail("Failed to get snapshots list for %s" % vm_name)
        if snapshot_name not in snapshots:
            test.fail("The snapshot '%s' was not in snapshot-list."
                      % snapshot_name)
        cmd_result = virsh.snapshot_dumpxml(vm_name, snapshot_name,
                                            **virsh_dargs)
        libvirt.check_result(cmd_result, expected_match=expected_str)

        cmd_result = virsh.snapshot_revert(vm_name, "", "--current",
                                           **virsh_dargs)
        libvirt.check_exit_status(cmd_result)

    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)

    cpu_mode = params.get('cpu_mode')
    expected_str_before_startup = params.get("expected_str_before_startup")
    expected_str_after_startup = params.get("expected_str_after_startup")
    test_operations = params.get("test_operations")
    status_error = "yes" == params.get("status_error", "no")
    err_msg = params.get("err_msg")

    bkxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    try:
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

        # Create cpu xml for test
        if vmxml.xmltreefile.find('cpu'):
            cpu_xml = vmxml.cpu
        else:
            cpu_xml = vm_xml.VMCPUXML()
        if cpu_mode:
            cpu_xml.mode = cpu_mode

        # Update vm's cpu
        vmxml.cpu = cpu_xml

        vmxml.sync()

        if expected_str_before_startup:
            libvirt.check_dumpxml(vm, expected_str_before_startup)

        if test_operations:
            for action in test_operations.split(","):
                if action == "do_snapshot":
                    do_snapshot(vm_name, expected_str_before_startup)

        # Check if vm could start successfully
        result = virsh.start(vm_name, debug=True)
        libvirt.check_exit_status(result)

        if expected_str_after_startup:
            libvirt.check_dumpxml(vm, expected_str_after_startup)

    finally:
        logging.debug("Recover test environment")
        if vm.is_alive():
            vm.destroy()

        libvirt.clean_up_snapshots(vm_name, domxml=bkxml)
        bkxml.sync()
