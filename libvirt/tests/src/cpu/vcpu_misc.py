import logging
import os
import re

from avocado.utils import process

from virttest import libvirt_version
from virttest import virsh
from virttest.libvirt_xml import domcapability_xml
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


def check_vm_cpu_model(vm_cpu_model, cmd_on_host, test):
    """
    Check if the vm cpu model is same with host cpu model

    :param vm_cpu_model: str, vm cpu model
    :param cmd_on_host: str, command to be executed on host
    :param test: test object
    """

    # Get host cpu model
    host_cpu_model = process.run(cmd_on_host,
                                 shell=True,
                                 verbose=True).stdout_text.strip()
    if host_cpu_model != vm_cpu_model:
        test.fail("VM cpu model '{}' should be same as "
                  "host's '{}'".format(vm_cpu_model, host_cpu_model))
    else:
        logging.debug("Host cpu model is same with vm cpu model as expected")


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

        if customize_cpu_features:
            for idx in range(len(cpu_xml.get_feature_list()) - 1, -1, -1):
                cpu_xml.remove_feature(idx)
            domcapa_xml = domcapability_xml.DomCapabilityXML()
            features = domcapa_xml.get_additional_feature_list(
                'host-model', ignore_features=None)
            for feature in features:
                for feature_name, feature_policy in feature.items():
                    # For host-passthrough mode, adding "invtsc" requires
                    # more settings, so it will be ignored.
                    if feature_name != "invtsc":
                        cpu_xml.add_feature(feature_name, feature_policy)

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

    def check_feature_list(vm, original_dict):
        """
        Compare new cpu feature list and original cpu

        :param vm: VM object
        :original_dict: Cpu feature dict , {"name1":"policy1","name2":"policy2"}
        """
        new_cpu_xml = vm_xml.VMXML.new_from_dumpxml(vm.name).cpu
        new_feature_dict = new_cpu_xml.get_dict_type_feature()
        if new_feature_dict != original_dict:
            test.fail('CPU feature lists are different, original is :%s,'
                      ' new is %s:' % (original_dict, new_feature_dict))

    libvirt_version.is_libvirt_feature_supported(params)
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
    cmd_in_guest = params.get("cmd_in_guest")
    customize_cpu_features = "yes" == params.get("customize_cpu_features", "no")
    bkxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    managed_save_file = "/var/lib/libvirt/qemu/save/%s.save" % vm_name

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
        cpu_xml = vmxml.cpu
        feature_dict = cpu_xml.get_dict_type_feature()

        if expected_str_before_startup:
            libvirt.check_dumpxml(vm, expected_str_before_startup)

        if test_operations:
            for action in test_operations.split(","):
                if action == "do_snapshot":
                    do_snapshot(vm_name, expected_str_before_startup)

        if virsh_edit_cmd:
            status = libvirt.exec_virsh_edit(vm_name, virsh_edit_cmd.split(","))
            if status == status_error:
                test.fail("Virsh edit got unexpected result.")

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
                status, output = vm_session.cmd_status_output(cmd_in_guest)
                if status:
                    vm_session.close()
                    test.fail("Failed to run '{}' in vm with "
                              "messages:\n{}".format(cmd_in_guest, output))
                vm_session.close()
                if cpu_mode == 'maximum':
                    check_vm_cpu_model(output.strip(), cmd_in_guest, test)

            # Add case: Check cpu xml after domain Managedsaved and restored
            if test_operations:
                for item in test_operations.split(','):
                    if item == "managedsave_restore":
                        # (1)Domain Manage saved
                        virsh.managedsave(vm_name, ignore_status=False, debug=True)
                        check_feature_list(vm, feature_dict)
                        # (2)Domain Restore
                        virsh.restore(managed_save_file, ignore_status=False, debug=True)
                        # (5)Check mode and feature list here
                        libvirt.check_dumpxml(vm, cpu_mode)
                        check_feature_list(vm, feature_dict)

    finally:
        logging.debug("Recover test environment")
        if os.path.exists(managed_save_file):
            virsh.managedsave_remove(vm_name, debug=True)
        if vm.is_alive():
            vm.destroy()
        libvirt.clean_up_snapshots(vm_name, domxml=bkxml)
        bkxml.sync()
