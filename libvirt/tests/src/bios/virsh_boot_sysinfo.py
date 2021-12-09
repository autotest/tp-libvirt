import logging
import os

from virttest import virsh
from virttest import utils_package
from virttest import libvirt_version
from virttest import data_dir
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


def check_in_vm(test, vm, **kwargs):
    """
    Check the output of fwcfg and smbios info in guest
    """
    sysinfo_type = kwargs.get("sysinfo_type")
    value_string = kwargs.get("value_string")
    entry_name = kwargs.get("entry_name")
    session = vm.wait_for_login()
    if sysinfo_type == "fwcfg":
        test_cmd = ("grep \"%s\" /sys/firmware/qemu_fw_cfg/by_name/%s/raw"
                    % (value_string, entry_name))
    status, output = session.cmd_status_output(test_cmd)
    logging.debug("CMD '%s' running result is:\n%s", test_cmd, output)
    if status:
        test.fail(output)
    if not output:
        test.fail("Gan't get correct sysinfo value in guest")
    session.close()
    return output


def run(test, params, env):
    """
    Test sysinfo in guest
    <sysinfo type='fwcfg'>...</sysinfo>
    <sysinfo type='smbios'>...</sysinfo>

    Steps:
    1) Edit VM XML for sysinfo element
    2) Verify if guest can boot as expected
    3) Check if the sysinfo is correct
    """

    vm_name = params.get("main_vm", "")
    vm = env.get_vm(vm_name)
    boot_type = params.get("boot_type", "")
    loader_type = params.get("loader_type")
    loader = params.get("loader")
    sysinfo_type = params.get("sysinfo_type", "")
    entry_name = params.get("entry_name")
    value_string = params.get("value_string")
    with_file = ("yes" == params.get("with_file", "no"))
    with_value = ("yes" == params.get("with_value", "no"))
    entry_file = os.path.join(data_dir.get_tmp_dir(), "provision.ign")
    err_msg = params.get("error_msg", "")
    status_error = ("yes" == params.get("status_error", "no"))
    without_name = ("yes" == params.get("without_name", "no"))

    if not libvirt_version.version_compare(6, 5, 0):
        test.cancel("FWCFG sysinfo is not supported in "
                    "current libvirt version")

    if (boot_type == "seabios" and
            not utils_package.package_install('seabios-bin')):
        test.cancel("Failed to install Seabios")

    if (boot_type == "ovmf" and
            not utils_package.package_install('OVMF')):
        test.cancel("Failed to install OVMF")

    # Back VM XML
    vmxml_backup = vm_xml.VMXML.new_from_dumpxml(vm_name)
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)

    try:
        # Specify boot loader for OVMF
        if boot_type == "ovmf":
            os_xml = vmxml.os
            os_xml.loader_type = loader_type
            os_xml.loader = loader
            os_xml.loader_readonly = "yes"
            vmxml.os = os_xml

        # Set attributes of fwcfg sysinfo in VMSysinfoXML
        if sysinfo_type == "fwcfg":
            sysinfo_xml = vm_xml.VMSysinfoXML()
            sysinfo_xml.type = sysinfo_type

            # Test with entry value in text
            if with_value:
                sysinfo_xml.entry_name = entry_name
                sysinfo_xml.entry = value_string

            # Test with entry file
            if with_file:
                with open('%s' % entry_file, 'w+') as f:
                    f.write('%s' % value_string)
                sysinfo_xml.entry_name = entry_name
                sysinfo_xml.entry_file = entry_file
            # Negative test without entry name
            elif without_name:
                sysinfo_xml.entry = value_string
            # Negative test without file in entry
            else:
                sysinfo_xml.entry_name = entry_name

        vmxml.sysinfo = sysinfo_xml
        logging.debug("New VM XML is:\n%s" % vmxml)
        ret = virsh.define(vmxml.xml)
        libvirt.check_result(ret, expected_fails=err_msg)
        result = virsh.start(vm_name, debug=True)
        libvirt.check_exit_status(result)

        if not status_error:
            # Check result in dumpxml and qemu cmdline
            if with_file:
                expect_xml_line = "<entry file=\"%s\" name=\"%s\" />" % (entry_file, entry_name)
                expect_qemu_line = "-fw_cfg name=%s,file=%s" % (entry_name, entry_file)
            if with_value:
                expect_xml_line = "<entry name=\"%s\">%s</entry>" % (entry_name, value_string)
                expect_qemu_line = "-fw_cfg name=%s,string=%s" % (entry_name, value_string)
            libvirt.check_dumpxml(vm, expect_xml_line)
            libvirt.check_qemu_cmd_line(expect_qemu_line)

            # Check result in guest
            kwargs = {"sysinfo_type": sysinfo_type,
                      "value_string": value_string,
                      "entry_name": entry_name}
            check_in_vm(test, vm, **kwargs)

    finally:
        logging.debug("Start to cleanup")
        if vm.is_alive():
            vm.destroy()
        logging.debug("Restore the VM XML")
        vmxml_backup.sync(options="--nvram")
        # Remove tmp file
        if os.path.exists(entry_file):
            os.remove(entry_file)
