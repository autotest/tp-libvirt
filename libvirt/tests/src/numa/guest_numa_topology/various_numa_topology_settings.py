#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Red Hat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Dan Zheng <dzheng@redhat.com>
#

import re

from virttest import libvirt_version
from virttest import test_setup
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt

from provider.numa import numa_base


def setup_default(test_obj):
    """
    Default setup function for the test

    :param test_obj: NumaTest object
    """
    test_obj.setup()
    hpc = test_setup.HugePageConfig(test_obj.params)

    numa_cells = eval(test_obj.params.get('numa_cells', '[]'))
    if numa_cells:
        #set target_hugepages according to the setting of 'numa_cells'.
        total_mem = 0
        for i in range(len(numa_cells)):
            total_mem += int(numa_cells[i]['memory'])
        test_obj.params["target_hugepages"] = int(total_mem/hpc.get_hugepage_size())
        hpc = test_setup.HugePageConfig(test_obj.params)

    hpc.setup()
    test_obj.params['hp_config_obj'] = hpc
    test_obj.test.log.debug("Step: setup is done")


def prepare_vm_xml(test_obj):
    """
    Customize the vm xml

    :param test_obj: NumaTest object
    :return: VMXML object updated
    """
    mb_dict = eval(test_obj.params.get('memory_backing', '{}'))
    numa_cells = eval(test_obj.params.get('numa_cells', '[]'))
    memAccess = test_obj.params.get('memAccess')
    discard = test_obj.params.get('discard')
    vcpu_num = int(test_obj.params.get('vcpu'))
    cpu_mode = test_obj.params.get('cpu_mode')
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(test_obj.vm.name)

    if numa_cells:
        if memAccess:
            numa_cells[0].update({'memAccess': memAccess})
        if discard:
            numa_cells[0].update({'discard': discard})
        if mb_dict:
            mem_backing = vm_xml.VMMemBackingXML()
            mem_backing.setup_attrs(**mb_dict)
            vmxml.mb = mem_backing
        if vmxml.xmltreefile.find('cpu'):
            cpuxml = vmxml.cpu
        else:
            cpuxml = vm_xml.VMCPUXML()

        cpuxml.mode = cpu_mode
        cpuxml.numa_cell = numa_cells
        test_obj.params['numa_cells'] = str(numa_cells)
        vmxml.cpu = cpuxml
    vmxml.vcpu = vcpu_num
    test_obj.test.log.debug("Step: vm xml before defining:\n%s", vmxml)
    return vmxml


def produce_expected_error(test_obj):
    """
    produce the expected error message

    :param test_obj: NumaTest object
    :return: str, error message to be checked
    """
    err_msg = test_obj.params.get('err_msg', '')
    if err_msg:
        numa_cells = eval(test_obj.params.get('numa_cells', '[]'))
        if numa_cells:
            test_obj.test.log.debug('numa_cells=%s', numa_cells)
            memaccess_invalid = numa_cells[0].get('memAccess') == 'invalid'
            attr_str = 'memAccess' if memaccess_invalid else 'discard'
            err_msg = err_msg % attr_str
    return err_msg


def check_qemu_cmdline(test_obj):
    """
    Check qemu command line

    :param test_obj: NumaTest object
    """
    pattern_in_qemu_cmdline = test_obj.params.get('pattern_in_qemu_cmdline')
    has_discard = test_obj.params.get('discard')
    if not has_discard:
        libvirt.check_qemu_cmd_line('discard-data', expect_exist=False)
    elif pattern_in_qemu_cmdline:
        libvirt.check_qemu_cmd_line(pattern_in_qemu_cmdline)
    test_obj.test.log.debug("Step: check qemu command line PASS")


def check_memory_share_setting(test_obj):
    """
    Check memory share setting in qemu_monitor_command result

    :param test_obj: NumaTest object
    """
    qemu_monitor_cmd = test_obj.params.get('qemu_monitor_cmd')
    qemu_monitor_option = test_obj.params.get('qemu_monitor_option')
    pattern_share_in_qmp_output = test_obj.params.get('pattern_share_in_qmp_output')
    test_obj.virsh_dargs.update({'ignore_status': False})
    ret = virsh.qemu_monitor_command(test_obj.vm.name,
                                     qemu_monitor_cmd,
                                     qemu_monitor_option,
                                     **test_obj.virsh_dargs,
                                     ).stdout_text.strip()

    if not re.search(pattern_share_in_qmp_output,
                     ret[ret.index("memory backend: ram-node0"):]):
        test_obj.test.fail("Expect '%s' exist, "
                           "but not found" % pattern_share_in_qmp_output)
    else:
        test_obj.test.log.debug("Step: check '%s' PASS", pattern_share_in_qmp_output)


def run_default(test_obj):
    """
    Default run function for the test

    :param test_obj: NumaTest object
    """
    test_obj.test.log.debug("Step: prepare vm xml")
    vmxml = prepare_vm_xml(test_obj)
    test_obj.virsh_dargs.update({'ignore_status': True})
    test_obj.test.log.debug("Step: define vm and check result")
    ret = virsh.define(vmxml.xml, **test_obj.virsh_dargs)
    err_msg_expected = produce_expected_error(test_obj)
    libvirt.check_result(ret, expected_fails=err_msg_expected)
    #  Some negative cases will fail to define the vm, so they will return
    #  to skip following steps
    if err_msg_expected:
        return

    test_obj.test.log.debug("Step: start vm")
    test_obj.vm.start()
    test_obj.test.log.debug("After vm is started, vm xml:\n"
                            "%s", vm_xml.VMXML.new_from_dumpxml(test_obj.vm.name))
    test_obj.vm.wait_for_login().close()
    check_qemu_cmdline(test_obj)
    check_memory_share_setting(test_obj)


def teardown_default(test_obj):
    """
    Default teardown function for the test

    :param test_obj: NumaTest object
    """
    test_obj.teardown()
    hpc = test_obj.params.get('hp_config_obj')
    if hpc:
        hpc.cleanup()
    test_obj.test.log.debug("Step: teardown is done")


def run(test, params, env):
    libvirt_version.is_libvirt_feature_supported(params)
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    numatest_obj = numa_base.NumaTest(vm, params, test)
    try:
        setup_default(numatest_obj)
        run_default(numatest_obj)
    finally:
        teardown_default(numatest_obj)
