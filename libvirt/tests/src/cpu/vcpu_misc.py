import logging
import re

from avocado.utils import process
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Test misc tests of virtual cpu features

    1) check dumpxml after snapshot-create/revert
    2) check vendor_id
    3) check maximum vcpus with topology settings

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
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
        if cpu_vendor_id:
            cpu_xml.vendor_id = cpu_vendor_id

        # Update vm's cpu
        vmxml.cpu = cpu_xml
        vmxml.sync()

        if vcpu_max:
            if with_topology:
                vm_xml.VMXML.set_vm_vcpus(vm_name, int(vcpu_max),
                                          cores=int(vcpu_max),
                                          sockets=1, threads=1,
                                          add_topology=with_topology,
                                          topology_correction=with_topology)
            else:
                vm_xml.VMXML.set_vm_vcpus(vm_name, int(vcpu_max))

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
    vcpu_max = params.get('vcpu_max')
    expected_str_before_startup = params.get("expected_str_before_startup")
    expected_str_after_startup = params.get("expected_str_after_startup")

    test_operations = params.get("test_operations")
    check_vendor_id = "yes" == params.get("check_vendor_id", "no")
    virsh_edit_cmd = params.get("virsh_edit_cmd")
    with_topology = "yes" == params.get("with_topology", "no")

    status_error = "yes" == params.get("status_error", "no")
    err_msg = params.get("err_msg")

    cpu_vendor_id = None
    expected_qemuline = None
    cmd_in_guest = None

    bkxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    try:
        if check_vendor_id:
            output = virsh.capabilities(debug=True)
            host_vendor = re.findall(r'<vendor>(\w+)<', output)[0]

            cpu_vendor_id = 'GenuineIntel'
            if host_vendor != "Intel":
                cpu_vendor_id = 'AuthenticAMD'
            logging.debug("Set cpu vendor_id to %s on this host.",
                          cpu_vendor_id)

            expected_qemuline = "vendor=" + cpu_vendor_id
            cmd_in_guest = ("cat /proc/cpuinfo | grep vendor_id | grep {}"
                            .format(cpu_vendor_id))

        # Update xml for test
        update_cpu_xml()

        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        logging.debug("Pre-test xml is %s", vmxml.xmltreefile)

        if expected_str_before_startup:
            libvirt.check_dumpxml(vm, expected_str_before_startup)

        if test_operations:
            for action in test_operations.split(","):
                if action == "do_snapshot":
                    do_snapshot(vm_name, expected_str_before_startup)

        if virsh_edit_cmd:
            status = libvirt.exec_virsh_edit(vm_name, virsh_edit_cmd.split(","))
            if status == status_error:
                test.fail("Virsh edit got unexpect result.")

        # Check if vm could start successfully
        if not status_error:
            result = virsh.start(vm_name, debug=True)
            libvirt.check_exit_status(result)

            if expected_str_after_startup:
                libvirt.check_dumpxml(vm, expected_str_after_startup)

            if expected_qemuline:
                libvirt.check_qemu_cmd_line(expected_qemuline)

            if cmd_in_guest:
                vm_session = vm.wait_for_login()
                if vm_session.cmd_status(cmd_in_guest):
                    vm_session.close()
                    test.fail("Failed to run '%s' in vm." % cmd_in_guest)
                vm_session.close()

    finally:
        logging.debug("Recover test environment")
        if vm.is_alive():
            vm.destroy()

        libvirt.clean_up_snapshots(vm_name, domxml=bkxml)
        bkxml.sync()
