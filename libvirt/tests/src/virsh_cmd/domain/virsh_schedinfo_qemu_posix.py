import re
import logging
import os

from virttest import virsh
from virttest import libvirt_version
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml import xcepts

from virttest.staging import utils_cgroup


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
        :return: False if expected controller is not mounted.
                 else return value's result object.
        """
        cgroup_path = \
            utils_cgroup.resolve_task_cgroup_path(vm.get_pid(), "cpu")
        logging.debug("cgroup_path=%s", cgroup_path)
        if not cgroup_type == "emulator":
            # When a VM has an 'emulator' child cgroup present, we must
            # strip off that suffix when detecting the cgroup for a machine
            if os.path.basename(cgroup_path) == "emulator":
                cgroup_path = os.path.dirname(cgroup_path)
            if cgroup_type == 'iothread':
                parameter = 'iothread1/%s' % parameter
            if cgroup_type == 'vcpu' and parameter != 'cpu.shares':
                parameter = 'vcpu0/%s' % parameter
            if parameter == 'cpu.shares' and libvirt_version.version_compare(7, 0, 0):
                cgroup_path = os.path.dirname(cgroup_path)
            logging.debug("cgroup_path is updated to '%s'", cgroup_path)
        cgroup_file = os.path.join(cgroup_path, parameter)
        logging.debug("cgroup_file=%s", cgroup_file)

        cg_file = None
        try:
            try:
                cg_file = open(cgroup_file)
                result = cg_file.read()
            except IOError:
                test.error("Failed to open cgroup file %s"
                           % cgroup_file)
        finally:
            if cg_file is not None:
                cg_file.close()
        return result.strip()

    def schedinfo_output_analyse(result, set_ref, scheduler="posix"):
        """
        Get the value of set_ref.

        :param result: CmdResult struct
        :param set_ref: the parameter has been set
        :param scheduler: the scheduler of qemu(default is posix)
        """
        output = result.stdout.strip()
        if not re.search("Scheduler", output):
            test.fail("Output is not standard:\n%s" % output)

        result_lines = output.splitlines()
        set_value_list = []
        for set_ref_node in set_ref.split(","):
            for line in result_lines:
                key_value = line.split(":")
                key = key_value[0].strip()
                value = key_value[1].strip()
                if key == "Scheduler":
                    if value != scheduler:
                        test.cancel("This test do not support"
                                    " %s scheduler." % scheduler)
                elif key == set_ref_node:
                    set_value_list.append(value)
                    break
        return set_value_list

    def get_current_value():
        """
        Get the current schedinfo value and return
        """
        current_result = virsh.schedinfo(vm_ref, " --current",
                                         ignore_status=True, debug=True)
        current_value = schedinfo_output_analyse(current_result, set_ref,
                                                 scheduler_value)
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
            logging.debug("After setting xml, VM XML:\n%s", vm_xml.VMXML.new_from_dumpxml(vm_name))

    vm = env.get_vm(vm_name)
    if vm.is_dead() and start_vm:
        vm.start()
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
            set_value_of_output = schedinfo_output_analyse(result, set_ref,
                                                           scheduler_value)

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
                          .format(result.stdout.strip()))
            if readonly:
                if not re.search(expect_msg, result.stderr.strip()):
                    test.fail("Fail to get expect err msg! "
                              "Expected: {} Actual: {}"
                              .foramt(expect_msg, result.stderr.strip()))
    finally:
        orig_config_xml.sync()
