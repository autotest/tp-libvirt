import re
import logging

from virttest import virsh
from virttest import libvirt_cgroup
from virttest import libvirt_version
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml import xcepts


def run(test, params, env):
    """
    Test command: virsh schedinfo.

    This version provide base test of virsh schedinfo command:
    virsh schedinfo <vm> [--set<set_ref>]
    TODO: to support more parameters.

    1) Get parameters and prepare vm's state
    2) Prepare test options.
    3) Run schedinfo command to set or get parameters.
    4) Get schedinfo in cgroup
    5) Recover environment like vm's state
    6) Check result.
    """
    def get_parameter_in_cgroup(vm, cgroup_type, parameter):
        """
        Get vm's cgroup value.

        :Param vm: the vm object
        :Param cgroup_type: type of cgroup we want, vcpu or emulator.
        :Param parameter: the cgroup parameter of vm which we need to get.
        :return: the value of parameter in cgroup.
        """
        vm_pid = vm.get_pid()
        cgtest = libvirt_cgroup.CgroupTest(vm_pid)
        cgroup_info = cgtest.get_standardized_cgroup_info("schedinfo")

        logging.debug("cgroup_info is %s" % cgroup_info)
        if parameter in ["cpu.cfs_period_us", "cpu.cfs_quota_us"]:
            if cgroup_type == "emulator":
                parameter = "%s/%s" % (cgroup_type, parameter)
            elif cgroup_type in ["vcpu", "iothread"]:
                parameter = "<%sX>/%s" % (cgroup_type, parameter)
        for key, value in libvirt_cgroup.CGROUP_V1_SCHEDINFO_FILE_MAPPING.items():
            if value == parameter:
                cgroup_ref_key = key
                break
        if 'cgroup_ref_key' not in locals():
            test.error("{} is not found in CGROUP_V1_SCHEDINFO_FILE_MAPPING."
                       .format(parameter))
        return cgroup_info[cgroup_ref_key]

    def analyse_schedinfo_output(result, set_ref):
        """
        Get the value of set_ref.

        :param result: CmdResult struct
        :param set_ref: the parameter has been set
        :return: the value of the parameter.
        """
        cg_obj = libvirt_cgroup.CgroupTest(None)
        output_dict = cg_obj.convert_virsh_output_to_dict(result)
        result_info = cg_obj.get_standardized_virsh_info("schedinfo", output_dict)
        set_value_list = []
        for set_ref_node in set_ref.split(","):
            if result_info.get(set_ref_node):
                set_value_list.append(result_info.get(set_ref_node))

        return set_value_list

    def get_current_value():
        """
        Get the current schedinfo value and return
        """
        current_result = virsh.schedinfo(vm_ref, " --current",
                                         ignore_status=True, debug=True)
        current_value = analyse_schedinfo_output(current_result, set_ref)
        return current_value

    # Prepare test options
    vm_ref = params.get("schedinfo_vm_ref", "domname")
    options_ref = params.get("schedinfo_options_ref", "")
    options_suffix = params.get("schedinfo_options_suffix", "")
    schedinfo_param = params.get("schedinfo_param", "vcpu")
    set_ref = params.get("schedinfo_set_ref", "")
    cgroup_ref = params.get("schedinfo_cgroup_ref", "cpu.shares")
    set_value = params.get("schedinfo_set_value", "")
    set_method = params.get("schedinfo_set_method", "cmd")
    set_value_expected = params.get("schedinfo_set_value_expected", "")
    # Libvirt version where function begins to change
    libvirt_ver_function_changed = eval(params.get(
        "libvirt_ver_function_changed", '[]'))
    # The default scheduler on qemu/kvm is posix
    scheduler_value = "posix"
    status_error = params.get("status_error", "no")
    start_vm = ("yes" == params.get("start_vm"))
    readonly = ("yes" == params.get("schedinfo_readonly", "no"))
    expect_msg = params.get("schedinfo_err_msg", "")

    if libvirt_cgroup.CgroupTest(None).is_cgroup_v2_enabled():
        if params.get("schedinfo_set_value_cgroupv2"):
            set_value = params.get("schedinfo_set_value_cgroupv2")
        if params.get("schedinfo_set_value_expected_cgroupv2"):
            set_value_expected = params.get(
                "schedinfo_set_value_expected_cgroupv2")
        if params.get("cgroup_v2_unsupported_reason"):
            test.cancel(params.get('cgroup_v2_unsupported_reason'))

    if libvirt_ver_function_changed:
        if not libvirt_version.version_compare(*libvirt_ver_function_changed):
            set_value = params.get("schedinfo_set_value_bk")
            set_value_expected = params.get("schedinfo_set_value_expected_bk")

    # Prepare vm test environment
    vm_name = params.get("main_vm")

    # For safety reasons, we'd better back up  xmlfile.
    orig_config_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    if not orig_config_xml:
        test.error("Backing up xmlfile failed.")

    if set_ref == "none":
        options_ref = "--set"
        set_ref = None
    elif set_ref:
        # Prepare vm xml for iothread test
        if schedinfo_param == 'iothread':
            virsh.iothreadadd(vm_name, '1', ignore_status=False, debug=True)
        if set_method == 'cmd':
            if set_value:
                set_ref_list = set_ref.split(",")
                set_value_list = set_value.split(",")
                for i in range(0, len(set_ref_list)):
                    if "--set" in options_ref:
                        options_ref += " %s=%s" % (set_ref_list[i], set_value_list[i])
                    else:
                        options_ref = "--set %s=%s" % (set_ref_list[i], set_value_list[i])
            else:
                options_ref = "--set %s" % set_ref
        elif set_method == 'xml':
            xml = vm_xml.VMXML.new_from_dumpxml(vm_name)
            try:
                cputune = xml.cputune
            except xcepts.LibvirtXMLNotFoundError:
                cputune = vm_xml.VMCPUTuneXML()
            name_map = {
                'cpu_shares': 'shares',
                'vcpu_period': 'period',
                'vcpu_quota': 'quota',
                'emulator_period': 'emulator_period',
                'emulator_quota': 'emulator_quota',
                'global_period': 'global_period',
                'global_quota': 'global_quota',
                'iothread_period': 'iothread_period',
                'iothread_quota': 'iothread_quota'
            }
            cputune[name_map[set_ref]] = int(set_value)
            xml.cputune = cputune
            xml.sync()
            logging.debug("After setting xml, VM XML:\n%s",
                          vm_xml.VMXML.new_from_dumpxml(vm_name))

    vm = env.get_vm(vm_name)
    if vm.is_dead() and start_vm:
        try:
            vm.start()
        except Exception as detail:
            orig_config_xml.sync()
            test.error(detail)

    domid = vm.get_id()
    domuuid = vm.get_uuid()

    if vm_ref == "domid":
        vm_ref = domid
    elif vm_ref == "domname":
        vm_ref = vm_name
    elif vm_ref == "domuuid":
        vm_ref = domuuid
    elif vm_ref == "hex_id":
        if domid == '-':
            vm_ref = domid
        else:
            vm_ref = hex(int(domid))

    options_ref += " %s " % options_suffix

    # Get schedinfo with --current parameter
    if set_ref and options_ref.count("config") and start_vm:
        bef_current_value = get_current_value()

    try:
        # Run command
        result = virsh.schedinfo(vm_ref, options_ref,
                                 ignore_status=True, debug=True, readonly=readonly)
        status = result.exit_status

        # VM must be running to get cgroup parameters.
        if not vm.is_alive():
            vm.start()

        if options_ref.count("config") and start_vm:
            # Get schedinfo with --current parameter
            aft_current_value = get_current_value()
            if bef_current_value != aft_current_value:
                test.fail("--config change the current %s" % set_ref)

            vm.destroy()
            vm.start()
            vm_ref = vm.get_id()

        if set_ref:
            start_current_value = get_current_value()

        set_value_of_cgroup = get_parameter_in_cgroup(vm, cgroup_type=schedinfo_param,
                                                      parameter=cgroup_ref)
        vm.destroy(gracefully=False)

        if set_ref:
            set_value_of_output = analyse_schedinfo_output(result, set_ref)

        # Check result
        if status_error == "no":
            if status:
                test.fail("Run failed with right command. Error: {}"
                          .format(result.stderr.strip()))
            else:
                if set_ref and set_value_expected:
                    logging.info("value will be set:%s\n"
                                 "set value in output:%s\n"
                                 "set value in cgroup:%s\n"
                                 "expected value:%s" % (
                                     set_value, set_value_of_output,
                                     set_value_of_cgroup, set_value_expected))
                    if set_value_of_output is None:
                        test.fail("Get parameter %s failed." % set_ref)
                    # Value in output of virsh schedinfo is not guaranteed 'correct'
                    # when we use --config.
                    # This is my attempt to fix it
                    # http://www.redhat.com/archives/libvir-list/2014-May/msg00466.html.
                    # But this patch did not go into upstream of libvirt.
                    # Libvirt just guarantee that the value is correct in next boot
                    # when we use --config. So skip checking of output in this case.
                    expected_value_list = sorted(set_value_expected.split(','))
                    if (not (expected_value_list == sorted(set_value_of_output)) and
                            not (options_ref.count("config"))):
                        test.fail("Run successful but value "
                                  "in output is not expected.")
                    if len(set_value_expected.split(',')) == 1:
                        if not (set_value_expected == set_value_of_cgroup):
                            test.fail("Run successful but value "
                                      "in cgroup is not expected.")
                        if not (expected_value_list == sorted(start_current_value)):
                            test.fail("Run successful but current "
                                      "value is not expected.")
        else:
            if not status:
                test.fail("Run successfully with wrong command. Output: {}"
                          .format(result.stdout_text.strip()))
            if not re.search(expect_msg, result.stderr_text.strip()):
                test.fail("Fail to get expect err msg! "
                          "Expected: {} Actual: {}"
                          .foramt(expect_msg, result.stderr_text.strip()))
    finally:
        orig_config_xml.sync()
