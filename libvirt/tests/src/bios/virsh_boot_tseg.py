import logging

from virttest import virsh
from virttest import utils_package
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt as utlv
from virttest.libvirt_xml import xcepts

from virttest import libvirt_version


def unify_to_MiB(unit, size):
    """
    Unify tseg size to MiB

    :param unit: the unit of tseg size
    :param size: string include size data
    :return: unit and the converted tseg size
    """
    if unit == "KiB":
        size = int(int(size) / 1024)
    elif unit == 'GiB':
        size = int(int(size) * 1024)
    return 'MiB', size


def run(test, params, env):
    """
    Test extended TSEG on Q35 machine types
    <smm state='on'>
        <tseg unit='MiB'>48</tseg>
    </smm>

    Steps:
    1) Edit VM xml for smm or tseg sub element
    2) Verify if Guest can boot as expected
    3) On i440 machine types, the property does not support.
       On Q35 machine types, both Seabios and OVMF Guest can bootup
    """

    vm_name = params.get("main_vm", "")
    vm = env.get_vm(vm_name)
    smm_state = params.get("smm_state", "off")
    unit = params.get("tseg_unit")
    size = params.get("tseg_size")
    boot_type = params.get("boot_type", "")
    loader_type = params.get("loader_type")
    loader = params.get("loader")
    err_msg = params.get("error_msg", "")
    vm_arch_name = params.get("vm_arch_name", "x86_64")
    status_error = ("yes" == params.get("status_error", "no"))

    if not libvirt_version.version_compare(4, 5, 0):
        test.cancel("TSEG does not support in "
                    "current libvirt version")

    if (boot_type == "seabios" and
            not utils_package.package_install('seabios-bin')):
        test.cancel("Failed to install Seabios")

    if (boot_type == 'ovmf' and
            not utils_package.package_install('OVMF')):
        test.cancel("Failed to install OVMF")

    # Back VM XML
    v_xml_backup = vm_xml.VMXML.new_from_dumpxml(vm_name)
    v_xml = vm_xml.VMXML.new_from_dumpxml(vm_name)

    try:
        # Specify boot loader for OVMF
        if boot_type == 'ovmf':
            os_xml = v_xml.os
            os_xml.loader_type = loader_type
            os_xml.loader = loader
            os_xml.loader_readonly = "yes"
            v_xml.os = os_xml

        try:
            features_xml = v_xml.features
        except xcepts.LibvirtXMLNotFoundError:
            if vm_arch_name == 'x86_64':
                # ACPI is required for UEFI on x86_64
                v_xml.xmltreefile.create_by_xpath("/features/acpi")
                features_xml = v_xml.features
            else:
                features_xml = vm_xml.VMFeaturesXML()

        features_xml.smm = smm_state
        if unit and size:
            features_xml.smm_tseg_unit = unit
            features_xml.smm_tseg = size
        v_xml.features = features_xml

        logging.debug("New VM XML is:\n%s", v_xml)
        ret = virsh.define(v_xml.xml)
        utlv.check_result(ret, expected_fails=err_msg)

        # Check result
        if not status_error:
            vm.start()
            if unit and size:
                # If tseg unit is KiB, convert it to MiB
                # as vm dumpxml convert it automatically
                if unit == 'KiB':
                    unit, size = unify_to_MiB(unit, size)
                expect_line = "<tseg unit=\"%s\">%s</tseg>" % (unit, size)
                utlv.check_dumpxml(vm, expect_line)
                # Qemu cmdline use mbytes
                unit, tseg_mbytes = unify_to_MiB(unit, size)
                expect_line = '-global mch.extended-tseg-mbytes=%s' % size
                utlv.check_qemu_cmd_line(expect_line)
    finally:
        logging.debug("Restore the VM XML")
        if vm.is_alive():
            vm.destroy()
        # OVMF enable nvram by default
        v_xml_backup.sync(options="--nvram")
