import re
import os
import logging
from xml.dom.minidom import parseString

from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest import data_dir


def run(test, params, env):
    """
    Test command: virsh cpu-baseline.

    Compute baseline CPU for a set of given CPUs.
    1.Get all parameters from configuration.
    2.Prepare a xml containing XML CPU descriptions.
    3.Perform virsh cpu-baseline operation.
    4.Confirm the test result.
    """

    def create_attach_xml(cpu_xmlfile, test_feature):
        """
        Prepare a xml containing XML CPU descriptions.

        :param cpu_xmlfile: XML file contains XML CPU descriptions.
        :param test_feature: test feature element.
        """
        content = """
 <cpu>
  <arch>x86_64</arch>
  <model>pentium3</model>
  <vendor>Intel</vendor>
  <feature name="ds"/>
  <feature name="%s"/>
 </cpu>
 <cpu>
  <arch>x86_64</arch>
  <model>pentium3</model>
  <vendor>Intel</vendor>
  <feature name="sse2"/>
  <feature name="%s"/>
  </cpu>
""" % (test_feature, test_feature)
        with open(cpu_xmlfile, 'w') as xmlfile:
            xmlfile.write(content)

    def check_xml(xml_output, test_feature):
        """
        Check if result output contains tested feature.

        :param xml_output: virsh cpu-baseline command's result.
        :param test_feature: Test feature element.
        """
        feature_name = ""
        dom = parseString(xml_output)
        feature = dom.getElementsByTagName("feature")
        for names in feature:
            feature_name += names.getAttribute("name")
        dom.unlink()
        if not re.search(test_feature, feature_name):
            test.fail("Cannot see '%s' feature" % test_feature)

    # Get all parameters.
    file_name = params.get("cpu_baseline_cpu_file", "cpu.xml")
    cpu_ref = params.get("cpu_baseline_cpu_ref", "file")
    extra = params.get("cpu_baseline_extra", "")
    test_feature = params.get("cpu_baseline_test_feature", "acpi")
    status_error = "yes" == params.get("status_error", "no")
    cpu_xmlfile = os.path.join(data_dir.get_tmp_dir(), file_name)

    # Prepare a xml file.
    create_attach_xml(cpu_xmlfile, test_feature)

    if cpu_ref == "file":
        cpu_ref = cpu_xmlfile
    cpu_ref = "%s %s" % (cpu_ref, extra)

    # Test.
    result = virsh.cpu_baseline(cpu_ref, ignore_status=True, debug=True)
    status = result.exit_status
    output = result.stdout.strip()
    if os.path.exists(cpu_xmlfile):
        os.remove(cpu_xmlfile)

    # Check status_error
    if status_error:
        if status == 0:
            test.fail("Run successfully with wrong command!")
        logging.debug("Command fail as expected")
    else:
        if status != 0:
            test.fail("Run failed with right command")
        check_xml(output, test_feature)

    # Use the output to config VM
    config_guest = "yes" == params.get("config_guest", "no")
    if config_guest and status == 0:
        vm_name = params.get("main_vm")
        vm = env.get_vm(vm_name)
        if vm.is_alive():
            vm.destroy(gracefully=False)
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        vmxml_backup = vmxml.copy()
        try:
            cpu_xml = vm_xml.VMCPUXML()
            cpu_xml['xml'] = output
            vmxml['cpu'] = cpu_xml
            vmxml.sync()
            cpu_model = cpu_xml['model']
            cpu_feature_list = cpu_xml.get_feature_list()
            result = virsh.start(vm_name, ignore_status=True, debug=True)
            libvirt.check_exit_status(result)
            vm_pid = vm.get_pid()
        except Exception:
            pass
        else:
            # Check qemu cmdline
            with open("/proc/%d/cmdline" % vm_pid) as vm_cmdline_file:
                vm_cmdline = vm_cmdline_file.read()
            if cpu_model in vm_cmdline:
                logging.debug("Find cpu model '%s' in VM cmdline", cpu_model)
            else:
                test.fail("Not find cpu model '%s' in VM cmdline" %
                          cpu_model)
            for feature in cpu_feature_list:
                feature_name = feature.get('name')
                if feature_name in vm_cmdline:
                    logging.debug("Find cpu feature '%s' in VM cmdline",
                                  feature_name)
                else:
                    test.fail("Not find cpu feature '%s' in VM "
                              "cmdline" % feature_name)
        finally:
            if vm.is_alive():
                vm.destroy(gracefully=False)
            vmxml_backup.sync()
