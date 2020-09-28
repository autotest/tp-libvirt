"""
Test libvirt support features in qemu cmdline.
BTW it not limited to hypervisors CPU/machine features.
"""
import re
import logging
import platform

from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt

from virttest import libvirt_version

from avocado.utils import process, astring


def config_feature_pv_eoi(test, vmxml, **kwargs):
    """
    Config libvirt VM XML to enable/disable PV EOI feature.

    :param vmxml: VMXML instance
    :param kwargs: Function keywords
    :return: Corresponding feature flag in qem cmdline
    """
    # This attribute supported since 0.10.2 (QEMU only)
    if not libvirt_version.version_compare(0, 10, 2):
        test.cancel("PV eoi is not supported in current"
                    " libvirt version")
    qemu_flags = []
    eoi_enable = kwargs.get('eoi_enable', 'on')
    get_hostos_version = astring.to_text(process.run("cat /etc/redhat-release", shell=True).stdout)
    if re.search(r'(\d+(\.\d+)?)', get_hostos_version) is not None:
        hostos_version = float(re.search(r'(\d+(\.\d+)?)', get_hostos_version).group(0))
        if hostos_version < float(8.1):
            if eoi_enable == 'on':
                qemu_flags.append('+kvm_pv_eoi')
            elif eoi_enable == 'off':
                qemu_flags.append('-kvm_pv_eoi')
            else:
                logging.error("Invaild value %s, eoi_enable must be 'on' or 'off'", eoi_enable)
        elif hostos_version > float(8.0):
            if eoi_enable == 'on':
                qemu_flags.append('kvm-pv-eoi=on')
            elif eoi_enable == 'off':
                qemu_flags.append('kvm-pv-eoi=off')
            else:
                logging.error("Invaild value %s, eoi_enable must be 'on' or 'off'", eoi_enable)
        else:
            test.fail("Can not decide the expected qemu cmd line because of no expected hostos version")

    # Create features tag if not existed
    if not vmxml.xmltreefile.find('features'):
        vmxml.features = vm_xml.VMFeaturesXML()
    vmxml_feature = vmxml.features
    if vmxml_feature.has_feature('apic'):
        vmxml_feature.remove_feature('apic')
    vmxml_feature.add_feature('apic', 'eoi', eoi_enable)
    vmxml.features = vmxml_feature
    logging.debug("Update VM XML:\n%s", vmxml)
    expect_fail = False if 'expect_define_vm_fail' not in kwargs \
        else kwargs['expect_define_vm_fail']
    result = virsh.define(vmxml.xml, debug=True)
    libvirt.check_exit_status(result, expect_fail)
    if expect_fail:
        libvirt.check_result(result, kwargs.get('expected_msg'))
        return
    return qemu_flags


def config_feature_memory_backing(test, vmxml, **kwargs):
    """
    Config libvirt VM XML to influence how virtual memory pages are backed
    by host pages.

    :param vmxml: VMXML instance
    :param kwargs: Function keywords
    :return: Corresponding feature flag in qem cmdline
    """
    # Both 'nosharepages' and 'locked' are supported since 1.0.6
    if not libvirt_version.version_compare(1, 0, 6):
        test.cancel("Element is not supported in current"
                    " libvirt version")
    qemu_flags = []
    no_sharepages = "yes" == kwargs.get("nosharepages", "no")
    locked = "yes" == kwargs.get("locked", "no")
    if no_sharepages:
        # On RHEL6, the flag is 'redhat-disable-KSM'
        # On RHEL7 & Fedora, the flag is 'mem-merge=off'
        qemu_flags.append(['mem-merge=off', 'redhat-disable-KSM'])
    if locked:
        if not libvirt_version.version_compare(5, 3, 0):
            qemu_flags.append("mlock=on")
        else:
            qemu_flags.append("mem-lock=on")
        memtune_xml = vm_xml.VMMemTuneXML()
        memtune_xml.hard_limit = vmxml.max_mem * 4
        vmxml.memtune = memtune_xml
        vmxml.sync()
    try:
        vm_xml.VMXML.set_memoryBacking_tag(vmxml.vm_name,
                                           hpgs=False,
                                           nosp=no_sharepages,
                                           locked=locked)
        logging.debug("xml updated to %s", vmxml.xmltreefile)
    except Exception as detail:
        logging.error("Update VM XML fail: %s", detail)
    return qemu_flags


def run(test, params, env):
    """
    Test libvirt support features in qemu cmdline.

    1) Config test feature in VM XML;
    2) Try to start VM;
    3) Check corresponding feature flags in qemu cmdline;
    4) Login VM to test feature if necessary.
    """
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    expect_fail = "yes" == params.get("expect_start_vm_fail", "no")
    expect_define_vm_fail = 'yes' == params.get('expect_define_vm_fail', 'no')
    test_feature = params.get("test_feature")
    # All test case Function start with 'test_feature' prefix
    testcase = globals()['config_feature_%s' % test_feature]
    test_feature_attr = params.get("test_feature_attr", '').split(",")
    test_feature_valu = params.get("test_feature_valu", '').split(",")
    # Paramters for test case
    if len(test_feature_attr) != len(test_feature_valu):
        test.error("Attribute number not match with value number")
    test_dargs = dict(list(zip(test_feature_attr, test_feature_valu)))
    if expect_define_vm_fail:
        test_dargs.update({'expect_define_vm_fail': expect_define_vm_fail,
                           'expected_msg': params.get('expected_msg', '')})
    if vm.is_alive():
        vm.destroy()
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    vmxml_backup = vmxml.copy()
    virsh_dargs = {'debug': True, 'ignore_status': False}

    if 'ppc64le' in platform.machine().lower() and test_feature == 'pv_eoi':
        if not libvirt_version.version_compare(6, 0, 0):
            test.cancel('Feature %s is supported since version 6.0.0' % test_feature)
    try:
        # Run test case
        qemu_flags = testcase(test, vmxml, **test_dargs)
        if not qemu_flags and expect_define_vm_fail:
            return
        result = virsh.start(vm_name, **virsh_dargs)
        libvirt.check_exit_status(result, expect_fail)

        # Check qemu flag
        vm_pid = vm.get_pid()
        with open("/proc/%s/cmdline" % vm_pid) as cmdline_f:
            cmdline_content = cmdline_f.read()
        logging.debug("VM cmdline:\n%s",
                      cmdline_content.replace('\x00', ' '))
        msg = "Find '%s' in qemu cmdline? %s"
        found_flags = []
        index = 0
        for flag in qemu_flags:
            # Here, flag could be a list, so uniform it to list for next
            # step check. And, check can pass if any element in the list
            # exist in cmdline
            if not isinstance(flag, list):
                flag = [flag]
            found_f = []
            for f in flag:
                if f in cmdline_content:
                    found_f.append(True)
                    break
                else:
                    found_f.append(False)
            found_flags.append(any(found_f))
            logging.info(msg % (flag, found_flags[index]))
            index += 1
        if False in found_flags:
            test.fail("Not find all flags")
    finally:
        vmxml_backup.sync()
