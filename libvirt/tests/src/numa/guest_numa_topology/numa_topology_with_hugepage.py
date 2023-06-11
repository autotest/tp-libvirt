#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Red Hat
#
#   SPDX-License-Identifier: GPL-2.0
#
#   Author: Dan Zheng <dzheng@redhat.com>
#

import re

from avocado.utils import process

from virttest import utils_libvirtd
from virttest import virsh
from virttest import test_setup
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt

from provider.numa import numa_base


def setup_hugepage(params):
    """
    Setup the huge page using given parameters

    :param params: dict, parameters to setup huge page
    :return: HugePageConfig object created
    """
    hpc = test_setup.HugePageConfig(params)
    hpc.setup()
    return hpc


def setup_default(test_obj):
    """
    Default setup function for the test

    :param test_obj: NumaTest object
    """
    test_obj.setup()
    params_2M = {'hugepage_size': '2048',
                 'hugepage_path': test_obj.params.get('hugepage_path_2M'),
                 'target_hugepages': test_obj.params.get('target_hugepages_2M')}
    params_1G = {'hugepage_size': '1048576',
                 'hugepage_path': test_obj.params.get('hugepage_path_1G'),
                 'target_hugepages': test_obj.params.get('target_hugepages_1G'),
                 'kernel_hp_file': '/sys/kernel/mm/hugepages/hugepages-1048576kB/nr_hugepages'}
    hp_size = test_obj.params.get('hp_size')
    if hp_size in ['2M', '2M_1G', 'scarce_mem']:
        test_obj.params.update(params_2M)
        hpconfig_2M = setup_hugepage(test_obj.params)
        test_obj.params['hp_config_2M'] = hpconfig_2M
    if hp_size in ['1G', '2M_1G']:
        test_obj.params.update(params_1G)
        hpconfig_1G = setup_hugepage(test_obj.params)
        test_obj.params['hp_config_1G'] = hpconfig_1G
    utils_libvirtd.Libvirtd().restart()

    test_obj.test.log.debug("Step: setup is done")


def prepare_vm_xml(test_obj):
    """
    Customize the vm xml

    :param test_obj: NumaTest object

    :return: VMXML object updated
    """
    vmxml = test_obj.prepare_vm_xml()
    test_obj.test.log.debug("Step: vm xml before defining:\n%s", vmxml)
    return vmxml


def produce_expected_error(test_obj):
    """
    produce the expected error message

    :param test_obj: NumaTest object

    :return: str, error message to be checked
    """
    hugepage_size = test_obj.params.get('hp_size')
    err_msg = ""
    if hugepage_size == 'scarce_mem':
        err_msg = "unable to map backing store for guest RAM: Cannot allocate memory"
    return err_msg


def verify_hugepage_memory_in_numa_maps(test_obj):
    """
    Verify hugepage memory in numa_maps is correct

    :param test_obj: NumaTest object
    """
    def _check_numa_maps(mem_size, hugepage_num, pagesize):

        cmd = 'grep -B1 %s /proc/`pidof qemu-kvm`/smaps' % mem_size
        out = process.run(cmd, shell=True, verbose=True).stdout_text.strip()
        matches = re.findall('(\w+)-', out)
        if not matches:
            test_obj.test.fail("Fail to find the pattern '(\w+)-' in smaps output '%s'" % out)
        cmd = 'grep %s /proc/`pidof qemu-kvm`/numa_maps' % matches[0]
        out_numa_maps = process.run(cmd, shell=True, verbose=True).stdout_text.strip()
        pat = r"huge anon=%s.*kernelpagesize_kB=%s" % (hugepage_num, pagesize)
        if not re.search(pat, out_numa_maps):
            test_obj.test.fail("Fail to find the pattern '%s' in "
                               "numa_maps output '%s'" % (pat,
                                                          out_numa_maps))
    hp_size = test_obj.params.get('hp_size')
    if hp_size in ['2M', '2M_1G']:
        _check_numa_maps(test_obj.params.get('vm_numa_node0_mem'),
                         test_obj.params.get('target_hugepages_2M'), '2048')
    if hp_size == '1G':
        _check_numa_maps(test_obj.params.get('vm_numa_node0_mem'),
                         test_obj.params.get('target_hugepages_1G'), '1048576')
    if hp_size == '2M_1G':
        _check_numa_maps(test_obj.params.get('vm_numa_node1_mem'),
                         test_obj.params.get('target_hugepages_1G'), '1048576')
    test_obj.test.log.debug("Step: verify hugepage memory in numa_maps: PASS")


def check_qemu_cmdline(test_obj):
    """
    Check qemu command line

    :param test_obj: NumaTest object
    """
    pat_in_qemu_cmdline = test_obj.params.get('pat_in_qemu_cmdline')
    hp_size = test_obj.params.get('hp_size')
    hugepage_path_1G = test_obj.params.get('hugepage_path_1G')
    hugepage_path_2M = test_obj.params.get('hugepage_path_2M')
    node0_mem_path = '%s/libvirt/qemu/.*' % hugepage_path_2M \
        if hp_size in ['2M', '2M_1G'] else '%s/libvirt/qemu/.*' % hugepage_path_1G
    node0_mem_size = int(test_obj.params.get('vm_numa_node0_mem')) * 1024
    node1_mem_path = '"mem-path":"%s/libvirt/qemu/.*",' % hugepage_path_1G \
        if hp_size == '2M_1G' else ''
    node1_prealloc = '"prealloc":true,' if hp_size == '2M_1G' else ''
    node1_mem_size = int(test_obj.params.get('vm_numa_node1_mem')) * 1024
    pat_in_qemu_cmdline = pat_in_qemu_cmdline % (node0_mem_path,
                                                 node0_mem_size,
                                                 node1_mem_path,
                                                 node1_prealloc,
                                                 node1_mem_size)
    test_obj.test.log.debug("The qemu command line "
                            "pattern:%s", pat_in_qemu_cmdline)
    libvirt.check_qemu_cmd_line(pat_in_qemu_cmdline)
    test_obj.test.log.debug("Step: check qemu command line: PASS")


def run_default(test_obj):
    """
    Default run function for the test

    :param test_obj: NumaTest object
    """
    test_obj.test.log.debug("Step: prepare vm xml")
    vmxml = prepare_vm_xml(test_obj)
    test_obj.test.log.debug("Step: define vm")
    ret = virsh.define(vmxml.xml, **test_obj.virsh_dargs)
    test_obj.test.log.debug("Step: start vm and check results")
    test_obj.virsh_dargs.update({'ignore_status': True})
    ret = virsh.start(test_obj.vm.name, **test_obj.virsh_dargs)
    err_msg_expected = produce_expected_error(test_obj)
    libvirt.check_result(ret, expected_fails=err_msg_expected)
    if err_msg_expected:
        return
    test_obj.test.log.debug("After vm is started, vm xml:\n"
                            "%s", vm_xml.VMXML.new_from_dumpxml(test_obj.vm.name))
    test_obj.vm.wait_for_login().close()
    check_qemu_cmdline(test_obj)
    verify_hugepage_memory_in_numa_maps(test_obj)


def teardown_default(test_obj):
    """
    Default teardown function for the test

    :param test_obj: NumaTest object
    """
    test_obj.teardown()
    hpconfig_2M = test_obj.params.get('hp_config_2M')
    hpconfig_1G = test_obj.params.get('hp_config_1G')
    for hpconfig in [hpconfig_2M, hpconfig_1G]:
        if hpconfig:
            hpconfig.cleanup()
    test_obj.test.log.debug("Step: hugepage is deallocated")
    test_obj.test.log.debug("Step: teardown is done")


def run(test, params, env):
    """
    Test for numa memory binding with emulator thread pin
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    numatest_obj = numa_base.NumaTest(vm, params, test)
    try:
        setup_default(numatest_obj)
        run_default(numatest_obj)
    finally:
        teardown_default(numatest_obj)
