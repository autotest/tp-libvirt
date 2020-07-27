import os
import logging

from virttest import virsh
from virttest import libvirt_version
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml import capability_xml
from virttest.libvirt_xml import domcapability_xml
from virttest.libvirt_xml.xcepts import LibvirtXMLNotFoundError


def get_domxml(cpu_mode, vm_name, extract=False):
    """
    Prepare domain xml according cpu mode

    :param cpu_mode: The name of cpu mode
    :param vm_name: The VM name
    :param extract: Setting True will extract cpu definition
                    from domain xml
    :return: The instance of VMXML or VMCPUXML
    """
    domxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    try:
        cpuxml = domxml.cpu
        if cpuxml.mode == cpu_mode:
            return cpuxml if extract else domxml
        else:
            del domxml["cpu"]
    except LibvirtXMLNotFoundError:
        pass  # CPU already does not exist

    # Create new CPUXML
    cpuxml = vm_xml.VMCPUXML()
    cpuxml.mode = cpu_mode
    if cpu_mode == "custom":
        cpuxml.model = "IvyBridge"
        cpuxml.match = "exact"
        cpuxml.check = "full"
        cpuxml.fallback = "forbid"
        cpuxml.add_feature("hypervisor", "require")

    if cpu_mode == "host-passthrough":
        cpuxml.check = "none"

    domxml.cpu = cpuxml
    return cpuxml if extract else domxml


def get_domcapa_xml(extract=False):
    """
    Get full domcapabilities xml or the cpu definition
    from domcapabilities xml

    :param extract: Setting True will extract cpu definition
                    from domcapabilities xml
    :return: The instance of DomCapabilityXML
    """
    domcapa_xml = domcapability_xml.DomCapabilityXML()
    if extract:
        domcapa_xml.xmltreefile = domcapa_xml.xmltreefile.reroot('/cpu')
    return domcapa_xml


def get_capa_xml(operate='', extract=False):
    """
    Get full capabilities xml or the cpu definition
    from capabilities xml

    :param operate: Operation mode, decide file's detail
    :param extract: Setting True means to extract cpu definition
                    from capabilities xml
    :return: The instance of CapabilityXML
    """
    capa_xml = capability_xml.CapabilityXML()
    if operate == 'delete':
        capa_xml.remove_feature(num=-1)
    if extract:
        capa_xml.xmltreefile = capa_xml.xmltreefile.reroot('/host/cpu')
    return capa_xml


def get_invalid_xml(data_xml):
    """
    Make invalid cpu xml by adding <host> outside of <cpu>

    :param data_xml: The instance of VMCPUXML
    :return: The instance of VMCPUXML
    """
    invalid_xml = vm_xml.VMCPUXML()
    with open(data_xml.xml, "r") as data_f, \
            open(invalid_xml.xml, "w") as new_f:
        # Discard line with <?xml
        data_f.readline()
        new_f.write("<host>{}</host>".format(data_f.read()))
    # Reload xml content
    invalid_xml.xmltreefile.parse(invalid_xml.xml)
    return invalid_xml


def run(test, params, env):
    """
    Test command: virsh cpu-compare with domain XML.

    Compare host CPU with a CPU described by an XML file.
    1.Get all parameters from configuration.
    2.Prepare temp file saves of CPU information.
    3.Perform virsh cpu-compare operation.
    4.Confirm the result.
    """

    # Get all parameters.
    file_type = params.get("compare_file_type", "domxml")
    is_extracted = params.get("extract_mode", False)
    cpu_mode = params.get("cpu_mode", "custom")
    cpu_compare_mode = params.get("cpu_compare_mode")
    status_error = ("yes" == params.get("status_error", "no"))
    tmp_file = ""

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    if not vm.is_alive():
        vm.start()

    if not libvirt_version.version_compare(4, 4, 0):
        test.cancel("CPU compare new update cases does not support"
                    " in this libvirt version")

    try:
        # Prepare temp compare file.
        cpu_compare_xml = ""
        if file_type not in ("domxml", "domcapa_xml", "capa_xml"):
            test.cancel("Unsupported xml type: %s" % file_type)

        if file_type == "domxml":
            cpu_compare_xml = get_domxml(cpu_mode, vm_name, is_extracted)

        if file_type == "domcapa_xml":
            cpu_compare_xml = get_domcapa_xml(is_extracted)

        if file_type == "capa_xml":
            cpu_compare_xml = get_capa_xml(cpu_compare_mode, is_extracted)

        if cpu_compare_mode == "invalid_test":
            cpu_compare_xml = get_invalid_xml(cpu_compare_xml)

        cpu_compare_xml.xmltreefile.write()
        tmp_file = cpu_compare_xml.xml
        with open(tmp_file) as tmp_file_f:
            logging.debug("CPU description XML:\n%s", tmp_file_f.read())

        # Perform virsh cpu-compare operation.
        result = virsh.cpu_compare(xml_file=tmp_file, ignore_status=True, debug=True)
        msg_pattern = params.get("msg_pattern")

        # Check result
        if status_error:
            if not result.exit_status:
                test.fail("Expect should fail but got:\n%s" % result.stdout)
        else:
            if result.exit_status:
                test.fail("Expect success but got:\n%s" % result.stderr)

        if msg_pattern:
            logging.debug("Expect key word in comand output: %s", msg_pattern)
            output = result.stdout.strip()
            if not output:
                output = result.stderr.strip()
            if not output.count(msg_pattern):
                test.fail("Not find expect key word in command output")
    finally:
        if os.path.exists(tmp_file):
            os.remove(tmp_file)
