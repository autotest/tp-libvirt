import os
import re
import logging

from virttest import virsh
from virttest import data_dir
from virttest import libvirt_version
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml import capability_xml
from virttest.libvirt_xml import domcapability_xml
from virttest.libvirt_xml.xcepts import LibvirtXMLNotFoundError


def get_domcapa_output(test):
    """
    Get output of virsh domcapabilities
    """
    ret = virsh.domcapabilities()
    if ret.exit_status:
        test.fail("Fail to run virsh domcapabilities: %s" % ret.stderr.strip())
    return ret.stdout.strip()


def get_cpu_definition(source_type, vm_name, test):
    """
    Extract cpu definition according source type

    :param source_type: The source file type includes
                        domxml, domcapa_xml and capa_xml
    :param vm_name: The VM name
    :param test: Avocado test object
    :return: The string of cpu definition
    """
    cpu_xml = None
    if source_type == "domxml":
        dom_xml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        cpu_xml = dom_xml.xmltreefile.get_element_string('/cpu')
    elif source_type == "domcapa_xml":
        domcapa_xml = domcapability_xml.DomCapabilityXML()
        cpu_tmp = vm_xml.VMCPUXML.from_domcapabilities(domcapa_xml)
        cpu_xml = cpu_tmp.xmltreefile.get_element_string('/')
    elif source_type == "capa_xml":
        capa_xml = capability_xml.CapabilityXML()
        cpu_xml = capa_xml.xmltreefile.get_element_string('/host/cpu')
    else:
        test.fail("Invalid source type: %s" % source_type)
    return cpu_xml


def run(test, params, env):
    """
    Test command: virsh hypervisor-cpu-compare

    Compare CPU provided by hypervisor on the host with a CPU described by an XML file
    1.Get all parameters from configuration.
    2.Prepare temp file saves of CPU information.
    3.Perform virsh hypervisor-cpu-compare operation.
    4.Confirm the result.
    """

    # Get all parameters.
    file_type = params.get("compare_file_type", "domxml")
    extract_mode = ("yes" == params.get("extract_mode", "no"))
    action_mode = params.get("action_mode")
    cpu_mode = params.get("cpu_mode", "custom")
    illegal_cpu_test = ("yes" == params.get("illegal_cpu_test", "no"))
    status_error = ("yes" == params.get("status_error", "no"))
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    option_str = params.get("hypv_cpu_compare_option")
    invalid_option = params.get("invalid_option")
    invalid_value = params.get("invalid_value")
    if invalid_option:
        option_str = option_str.replace(invalid_option, "")

    if not libvirt_version.version_compare(4, 4, 0):
        test.cancel("hypervisor-cpu-compare does not support"
                    " in this libvirt version")

    if not vm.is_alive():
        vm.start()

    baseline_provider = params.get("baseline_provider", "libvirt")
    dom_xml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    backup_xml = dom_xml.copy()
    compare_file = os.path.join(data_dir.get_tmp_dir(), "cpu.xml")
    domcapa_file = os.path.join(data_dir.get_tmp_dir(), "tmp_file")

    def get_domain_output(cpu_mode):
        """
        Prepare domain xml according cpu mode

        :param cpu_mode: The name of cpu mode
        :return: The domain xml data
        """
        try:
            cpuxml = dom_xml.cpu
            if not action_mode and cpuxml.mode == cpu_mode:
                return dom_xml.xmltreefile.get_element_string("/cpu")
            else:
                del dom_xml["cpu"]
        except LibvirtXMLNotFoundError:
            pass  # CPU already does not exist

        # Create new CPUXML
        cpuxml = vm_xml.VMCPUXML()
        cpuxml.mode = cpu_mode
        if cpu_mode == "custom":
            # Customize cpu according domcapabilities xml
            domcapa_output = get_domcapa_output(test)
            with open(domcapa_file, "w+") as tmp_f:
                tmp_f.write(domcapa_output)
            if "hypervisor" in baseline_provider:
                ret = virsh.hypervisor_cpu_baseline(domcapa_file)
            else:
                ret = virsh.cpu_baseline(domcapa_file)
            if ret.exit_status:
                test.fail("Fail to run virsh (hypervisor-)cpu-baseline: %s"
                          % ret.stderr.strip())
            cpuxml.xml = ret.stdout.strip()

        if cpu_mode == "host-passthrough":
            cpuxml.check = "none"

        dom_xml.cpu = cpuxml
        dom_xml.sync()
        vm.start()
        # VM start will change domxml content
        v_xml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        return v_xml.xmltreefile.get_element_string("/cpu")

    def get_options(option_str):
        """
        Prepare virsh cmd options according input string

        :param option_str: Input option string contains option names
        :return: Virsh cmd options
        """
        options = ""
        emulator = dom_xml.devices.by_device_tag('emulator')[0]
        osxml = dom_xml.os
        if option_str.count("--virttype"):
            options += "--virttype %s " % dom_xml.hypervisor_type
        if option_str.count("--emulator"):
            options += "--emulator %s " % emulator.path
        if option_str.count("--arch"):
            options += "--arch %s " % osxml.arch
        if option_str.count("--machine"):
            options += "--machine %s " % osxml.machine
        if option_str.count("--error"):
            options += "--error "
        return options

    try:
        # Prepare options
        options = get_options(option_str)
        if invalid_option:
            options += "%s %s " % (invalid_option, invalid_value)

        # Prepare cpu compare file.
        cpu_data = ""
        if file_type == "domxml":
            cpu_data = get_domain_output(cpu_mode)
        elif file_type == "domcapa_xml":
            cpu_data = get_domcapa_output(test)
        elif file_type == "capa_xml":
            cpu_data = virsh.capabilities()
        else:
            test.error("The compare file type %s is unsupported" % file_type)

        # Extract cpu definition
        if extract_mode:
            cpu_data = get_cpu_definition(file_type, vm_name, test)
            if illegal_cpu_test and file_type == "domxml":
                # Make invalid cpu data by adding <host> outside of <cpu>
                cpu_data = "<host>{}</host>".format(cpu_data)

        with open(compare_file, "w+") as compare_file_f:
            compare_file_f.write(cpu_data)
            compare_file_f.flush()
            compare_file_f.seek(0)
            logging.debug("CPU description XML:\n%s", compare_file_f.read())

        # Perform virsh cpu-compare operation.
        result = virsh.hypervisor_cpu_compare(xml_file=compare_file, options=options, ignore_status=True, debug=True)
        msg_pattern = params.get("msg_pattern")

        # Check result
        if status_error and not result.exit_status:
            test.fail("Expect should fail but got:\n%s" % result.stdout)
        elif not status_error and result.exit_status:
            test.fail("Expect success but got:\n%s" % result.stderr)

        if msg_pattern:
            logging.debug("Expect key word in comand output: %s", msg_pattern)
            output = result.stdout.strip()
            if not output:
                output = result.stderr.strip()
            if not re.findall(msg_pattern, output):
                test.fail("Not find expect key word '%s' in command "
                          "output '%s'" % (msg_pattern, output))
    finally:
        backup_xml.sync()
        if os.path.exists(domcapa_file):
            os.remove(domcapa_file)
        if os.path.exists(compare_file):
            os.remove(compare_file)
