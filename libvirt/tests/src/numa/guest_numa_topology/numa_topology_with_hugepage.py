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

from avocado.utils import memory
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
    memory_backing = eval(test_obj.params.get('memory_backing', '{}'))
    if memory_backing:
        numa_base.check_hugepage_availability(memory_backing["hugepages"]["pages"])
    test_obj.setup()
    hp_size = test_obj.params.get('hp_size')
    hpc_list = []
    if hp_size in ['default_hugepage', 'scarce_mem']:
        numa_base.adjust_parameters(test_obj.params,
                                    hugepage_mem=int(test_obj.params.get('hugepage_mem')))
        hpconfig = setup_hugepage(test_obj.params)
        hpc_list.append(hpconfig)
    mapping_hp_kib = {'2M': 2048, '512M': 524288, '1G': 1048576}
    for pg_size in ['2M', '512M', '1G']:
        if hp_size.count(pg_size):
            hugepage_size = mapping_hp_kib[pg_size]
            hugepage_mem_by_size = eval(test_obj.params.get("hugepage_mem_by_size"))
            new_params = test_obj.params.copy()
            numa_base.adjust_parameters(new_params,
                                        hugepage_size=hugepage_size,
                                        hugepage_mem=hugepage_mem_by_size[hugepage_size])
            hpconfig = setup_hugepage(new_params)
            hpc_list.append(hpconfig)
            test_obj.params[pg_size] = new_params
    test_obj.params['hpc_list'] = hpc_list
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
    params_2M = test_obj.params.get('2M')
    params_512M = test_obj.params.get('512M')
    params_1G = test_obj.params.get('1G')
    if hp_size in ['default_hugepage']:
        _check_numa_maps(test_obj.params.get('vm_numa_node0_mem'),
                         test_obj.params.get('target_hugepages'), str(memory.get_huge_page_size()))
    if hp_size in ['512M_2M']:
        _check_numa_maps(test_obj.params.get('vm_numa_node0_mem'),
                         params_512M.get('target_hugepages'), '524288')
        _check_numa_maps(test_obj.params.get('vm_numa_node1_mem'),
                         params_2M.get('target_hugepages'), '2048')
    if hp_size in ['2M_1G']:
        _check_numa_maps(test_obj.params.get('vm_numa_node0_mem'),
                         params_2M.get('target_hugepages'), '2048')
        _check_numa_maps(test_obj.params.get('vm_numa_node1_mem'),
                         params_1G.get('target_hugepages'), '1048576')
    if hp_size == '1G':
        _check_numa_maps(test_obj.params.get('vm_numa_node0_mem'),
                         params_1G.get('target_hugepages'), '1048576')

    test_obj.test.log.debug("Step: verify hugepage memory in numa_maps: PASS")


def check_qemu_cmdline(test_obj):
    """
    Check qemu command line

    :param test_obj: NumaTest object
    """
    pat_in_qemu_cmdline = test_obj.params.get('pat_in_qemu_cmdline')
    hp_size = test_obj.params.get('hp_size')
    params_2M = test_obj.params.get('2M')
    params_512M = test_obj.params.get('512M')
    params_1G = test_obj.params.get('1G')
    node1_mem_path = ''
    if hp_size == 'default_hugepage':
        node0_mem_path = '%s/libvirt/qemu/.*' % test_obj.params['vm_hugepage_mountpoint']
    elif hp_size == '512M_2M':
        node0_mem_path = '%s/libvirt/qemu/.*' % params_512M.get('vm_hugepage_mountpoint')
        node1_mem_path = '"mem-path":"%s/libvirt/qemu/.*",' % params_2M.get('vm_hugepage_mountpoint')
    elif hp_size == '2M_1G':
        node0_mem_path = '%s/libvirt/qemu/.*' % params_2M.get('vm_hugepage_mountpoint')
        node1_mem_path = '"mem-path":"%s/libvirt/qemu/.*",' % params_1G.get('vm_hugepage_mountpoint')
    elif hp_size == '1G':
        node0_mem_path = '%s/libvirt/qemu/.*' % params_1G.get('vm_hugepage_mountpoint')

    node0_mem_size = int(test_obj.params.get('vm_numa_node0_mem')) * 1024
    node1_prealloc = '"prealloc":true,' if hp_size == '2M_1G' or hp_size == '512M_2M' else ''
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
    err_msg_expected = test_obj.params.get("err_msg")
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
