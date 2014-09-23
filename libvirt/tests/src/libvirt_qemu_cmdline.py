"""
Test libvirt support features in qemu cmdline.
BTW it not limited to hypervisors CPU/machine features.
"""
import logging
from autotest.client.shared import error
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from provider import libvirt_version


def config_feature_pv_eoi(vmxml, **kwargs):
    """
    Config libvirt VM XML to enable/disable PV EOI feature.

    :param vmxml: VMXML instance
    :param kwargs: Function keywords
    :return: Corresponding feature flag in qem cmdline
    """
    # This attribute supported since 0.10.2 (QEMU only)
    if not libvirt_version.version_compare(0, 10, 2):
        raise error.TestNAError("PV eoi is not supported in current"
                                " libvirt version")
    qemu_flag = ''
    eoi_enable = kwargs.get('eoi_enable', 'on')
    if eoi_enable == 'on':
        qemu_flag = '+kvm_pv_eoi'
    elif eoi_enable == 'off':
        qemu_flag = '-kvm_pv_eoi'
    else:
        logging.error("Invaild value %s, eoi_enable must be 'on' or 'off'",
                      eoi_enable)
    try:
        vmxml_feature = vmxml.features
        if vmxml_feature.has_feature('apic'):
            vmxml_feature.remove_feature('apic')
        vmxml_feature.add_feature('apic', 'eoi', eoi_enable)
        vmxml.features = vmxml_feature
        logging.debug("Update VM XML:\n%s", vmxml)
        vmxml.sync()
    except Exception, detail:
        logging.error("Update VM XML fail: %s", detail)
    return qemu_flag


def run(test, params, env):
    """
    Test libvirt support features in qemu cmdline.

    1) Config test feature in VM XML;
    2) Try to start VM;
    3) Check corresponding feature flags in qemu cmdline;
    4) Login VM to test feature if necessary.
    """
    vm_name = params.get("main_vm", "virt-tests-vm1")
    vm = env.get_vm(vm_name)
    expect_fail = "yes" == params.get("expect_start_vm_fail", "no")
    test_feature = params.get("test_feature")
    test_feature_attr = params.get("test_feature_attr", '')
    test_feature_valu = params.get("test_feature_valu", '')
    # All test case Function start with 'test_feature' prefix
    testcase = globals()['config_feature_%s' % test_feature]
    # Paramters for test case
    test_dargs = {test_feature_attr: test_feature_valu}
    if vm.is_alive():
        vm.destroy()
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    vmxml_backup = vmxml.copy()
    virsh_dargs = {'debug': True, 'ignore_status': False}
    try:
        # Run test case
        qemu_flag = testcase(vmxml, **test_dargs)
        result = virsh.start(vm_name, **virsh_dargs)
        libvirt.check_exit_status(result, expect_fail)

        # Check qemu flag
        vm_pid = vm.get_pid()
        cmdline_f = open("/proc/%s/cmdline" % vm_pid)
        cmdline_content = cmdline_f.read()
        cmdline_f.close()
        logging.debug("VM cmdline:\n%s",
                      cmdline_content.replace('\x00', ' '))
        if qemu_flag in cmdline_content:
            logging.info("Find '%s' in qemu cmdline", qemu_flag)
        else:
            raise error.TestFail("Not find '%s' in qemu cmdline", qemu_flag)
    finally:
        vmxml_backup.sync()
