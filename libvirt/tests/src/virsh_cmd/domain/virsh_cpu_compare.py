import os
import logging
import platform

from virttest import data_dir
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml import capability_xml
from virttest.libvirt_xml.xcepts import LibvirtXMLNotFoundError
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Test command: virsh cpu-compare.

    Compare host CPU with a CPU described by an XML file.
    1.Get all parameters from configuration.
    2.Prepare temp file saves of CPU information.
    3.Perform virsh cpu-compare operation.
    4.Confirm the result.
    """

    def get_cpu_xml(target, mode):
        """
        Get CPU information and put it into a file.

        :param target: Test target, host or guest's cpu description.
        :param mode: Test mode, decides file's detail.
        """
        libvirtxml = vm_xml.VMCPUXML()
        if target == "host":
            cpu_feature_list = host_cpu_xml.get_feature_list()
            if cpu_match:
                libvirtxml['match'] = cpu_match
            libvirtxml['vendor'] = host_cpu_xml['vendor']
            libvirtxml['model'] = host_cpu_xml['model']
            for cpu_feature in cpu_feature_list:
                feature_name = cpu_feature.get('name')
                libvirtxml.add_feature(feature_name, "require")
        else:
            try:
                libvirtxml = vmxml['cpu']
            except LibvirtXMLNotFoundError:
                test.cancel("No <cpu> element in domain XML")

        if mode == "modify":
            if modify_target == "mode":
                libvirtxml['mode'] = modify_value
                # Power CPU model names are in lowercases for compatibility mode
                if "ppc" in platform.machine() and modify_value == "host-model":
                    libvirtxml['model'] = libvirtxml['model'].lower()
            elif modify_target == "model":
                libvirtxml['model'] = modify_value
            elif modify_target == "vendor":
                libvirtxml['vendor'] = modify_value
            elif modify_target == "feature_name":
                if modify_value == "REPEAT":
                    feature_name = libvirtxml.get_feature_name(feature_num)
                    feature_policy = libvirtxml.get_feature_policy(0)
                    libvirtxml.add_feature(feature_name, feature_policy)
                else:
                    libvirtxml.set_feature(feature_num, name=modify_value)
            elif modify_target == "feature_policy":
                libvirtxml.set_feature(feature_num, policy=modify_value)
        elif mode == "delete":
            libvirtxml.remove_feature(feature_num)
        else:
            pass
        return libvirtxml

    # Get all parameters.
    ref = params.get("cpu_compare_ref")
    mode = params.get("cpu_compare_mode", "")
    modify_target = params.get("cpu_compare_modify_target", "")
    modify_value = params.get("cpu_compare_modify_value", "")
    feature_num = int(params.get("cpu_compare_feature_num", -1))
    target = params.get("cpu_compare_target", "host")
    extra = params.get("cpu_compare_extra", "")
    file_name = params.get("cpu_compare_file_name", "cpu.xml")
    cpu_match = params.get("cpu_compare_cpu_match", "")
    modify_invalid = "yes" == params.get("cpu_compare_modify_invalid", "no")
    check_vm_ps = "yes" == params.get("check_vm_ps", "no")
    check_vm_ps_value = params.get("check_vm_ps_value")
    tmp_file = os.path.join(data_dir.get_tmp_dir(), file_name)

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    if vm.is_alive():
        vm.destroy()
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    vmxml_backup = vmxml.copy()
    host_cpu_xml = capability_xml.CapabilityXML()

    try:
        # Add cpu element if it not present in VM XML
        if not vmxml.get('cpu'):
            new_cpu = vm_xml.VMCPUXML()
            new_cpu['model'] = host_cpu_xml['model']
            vmxml['cpu'] = new_cpu
        # Add cpu model element if it not present in VM XML
        if not vmxml['cpu'].get('model'):
            vmxml['cpu']['model'] = host_cpu_xml['model']
        # Prepare VM cpu feature if necessary
        if modify_target in ['feature_name', 'feature_policy', 'delete']:
            if len(vmxml['cpu'].get_feature_list()) == 0 and host_cpu_xml.get_feature_list():
                # Add a host feature to VM for testing
                vmxml_cpu = vmxml['cpu'].copy()
                vmxml_cpu.add_feature(host_cpu_xml.get_feature_name('-1'))
                vmxml['cpu'] = vmxml_cpu
                vmxml.sync()
            else:
                test.cancel("No features present in host "
                            "capability XML")

        # Prepare temp compare file.
        cpu_compare_xml = get_cpu_xml(target, mode)
        with open(tmp_file, 'w+') as cpu_compare_xml_f:
            if mode == "clear":
                cpu_compare_xml_f.truncate(0)
            else:
                cpu_compare_xml.xmltreefile.write(cpu_compare_xml_f)
            cpu_compare_xml_f.seek(0)
            logging.debug("CPU description XML:\n%s", cpu_compare_xml_f.read())

        # Expected possible result msg patterns and exit status
        msg_patterns = []
        if not mode:
            if target == "host":
                msg_patterns = ["identical"]
            else:
                # As we don't know the <cpu> element in domain,
                # so just check command exit status
                pass
        elif mode == "delete":
            if cpu_match == "strict":
                msg_patterns = ["incompatible"]
            else:
                msg_patterns = ["superset"]
        elif mode == "modify":
            if modify_target == "mode":
                if modify_invalid:
                    msg_patterns = ["Invalid mode"]
            elif modify_target == "model":
                if modify_invalid:
                    msg_patterns = ["Unknown CPU model"]
            elif modify_target == "vendor":
                if modify_invalid:
                    msg_patterns = ["incompatible"]
            elif modify_target == "feature_name":
                if modify_value == "REPEAT":
                    msg_patterns = ["more than once"]
                elif modify_value == "ia64":
                    msg_patterns = ["incompatible"]
                elif modify_invalid:
                    msg_patterns = ["Unknown"]
            elif modify_target == "feature_policy":
                if modify_value == "forbid":
                    msg_patterns = ["incompatible"]
                else:
                    msg_patterns = ["identical"]
            else:
                test.cancel("Unsupport modify target %s in this "
                            "test" % mode)
        elif mode == "clear":
            msg_patterns = ["empty", "does not contain any"]
        elif mode == "invalid_test":
            msg_patterns = []
        else:
            test.cancel("Unsupport modify mode %s in this "
                        "test" % mode)
        status_error = params.get("status_error", "")
        if status_error == "yes":
            expected_status = 1
        elif status_error == "no":
            expected_status = 0
        else:
            # If exit status is not specified in cfg, using msg_patterns
            # to get expect exit status
            if [item for item in msg_patterns if item in ['identical', 'superset']]:
                expected_status = 0
            else:
                expected_status = 1
            # Default guest cpu compare should pass
            if not mode and target == "guest":
                expected_status = 0

        if ref == "file":
            ref = tmp_file
        ref = "%s %s" % (ref, extra)

        # Perform virsh cpu-compare operation.
        result = virsh.cpu_compare(ref, ignore_status=True, debug=True)

        if os.path.exists(tmp_file):
            os.remove(tmp_file)
        # Check result
        logging.debug("Expect command exit status: %s", expected_status)
        if result.exit_status != expected_status:
            test.fail("Exit status %s is not expected"
                      % result.exit_status)
        if msg_patterns:
            logging.debug("Expect key word in comand output: %s", msg_patterns)
            if result.stdout.strip():
                output = result.stdout.strip()
            else:
                output = result.stderr.strip()

            if not [item for item in msg_patterns if output.count(item)]:
                test.fail("Not find expect key word in command output")

        # Check VM for cpu 'mode' related cases
        if check_vm_ps:
            vmxml['cpu'] = cpu_compare_xml
            vmxml.sync()
            result = virsh.start(vm_name, ignore_status=True, debug=True)
            libvirt.check_exit_status(result)
            vm_pid = vm.get_pid()
            if vm_pid is None:
                test.error("Could not get VM pid")
            with open("/proc/%d/cmdline" % vm_pid) as vm_cmdline_file:
                vm_cmdline = vm_cmdline_file.read()
            vm_features_str = ""
            # cmdline file is always separated by NUL characters('\x00')
            for item in vm_cmdline.split('\x00-'):
                if item.count('cpu'):
                    vm_features_str = item
            logging.debug("VM cpu device: %s", vm_features_str)
            vm_features = []
            for f in vm_features_str.split(','):
                if f.startswith('+'):
                    vm_features.append(f[1:])
            host_features = []
            if check_vm_ps_value == 'CAPABILITY':
                for feature in host_cpu_xml.get_feature_list():
                    host_features.append(feature.get('name'))
            else:
                host_features.append(check_vm_ps_value)
            for feature in vm_features:
                if feature not in host_features:
                    test.fail("Not find %s in host capability"
                              % feature)
    finally:
        vmxml_backup.sync()
