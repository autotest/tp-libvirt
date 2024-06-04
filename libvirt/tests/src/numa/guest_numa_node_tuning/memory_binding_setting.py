#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Red Hat
#
#   SPDX-License-Identifier: GPL-2.0
#
#   Author: Dan Zheng <dzheng@redhat.com>
#


import os
import re
import platform

from avocado.utils import memory
from avocado.utils import process

from virttest import libvirt_cgroup
from virttest import libvirt_version
from virttest import virsh
from virttest import test_setup
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_numa

from provider.numa import numa_base


def setup_default(test_obj):
    """
    Default setup function for the test

    :param test_obj: NumaTest object
    """
    test_obj.setup()
    if test_obj.params.get('memory_backing'):
        numa_base.adjust_parameters(test_obj.params,
                                    hugepage_mem=int(test_obj.params.get("hugepage_mem")))
        expected_hugepage_size = test_obj.params.get('expected_hugepage_size')
        hpc = test_setup.HugePageConfig(test_obj.params)
        all_nodes = test_obj.online_nodes_withmem
        target_hugepages = test_obj.params.get('target_hugepages')
        hpc.set_node_num_huge_pages(target_hugepages, all_nodes[0], expected_hugepage_size)
        hpc.set_node_num_huge_pages(target_hugepages, all_nodes[1], expected_hugepage_size)
        test_obj.test.log.debug("Get first node hugepage is "
                                "%d", hpc.get_node_num_huge_pages(all_nodes[0],
                                                                  expected_hugepage_size))
        test_obj.test.log.debug("Get second node hugepage is "
                                "%d", hpc.get_node_num_huge_pages(all_nodes[1],
                                                                  expected_hugepage_size))
        test_obj.params['hp_config_obj'] = hpc
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
    err_msg = test_obj.produce_expected_error()
    mem_mode = test_obj.params.get('mem_mode')
    single_host_node = test_obj.params.get('single_host_node') == 'yes'
    memory_backing = eval(test_obj.params.get('memory_backing', '{}'))

    if mem_mode in ['strict', 'restrictive'] and single_host_node and memory_backing:
        new_err_msg = "unable to map backing store for guest RAM: Cannot allocate memory"
        err_msg = "%s|%s" % (err_msg, new_err_msg) if err_msg else new_err_msg
    return err_msg


def verify_host_numa_memory_allocation(test_obj):
    """
    Verify the host numa node memory is allocated as requested by vm

    :param test_obj: NumaTest object
    """
    single_host_node = test_obj.params.get('single_host_node') == 'yes'
    memory_backing = test_obj.params.get('memory_backing')
    mem_mode = test_obj.params.get('mem_mode')
    mem_size = eval(test_obj.params.get('vm_attrs'))['memory']
    cmd = 'grep -B1 %s /proc/`pidof qemu-kvm`/smaps' % mem_size
    out = process.run(cmd, shell=True, verbose=True).stdout_text.strip()
    matches = re.findall('(\w+)-', out)
    cmd = 'grep %s /proc/`pidof qemu-kvm`/numa_maps' % matches[0]
    out_numa_maps = process.run(cmd, shell=True, verbose=True).stdout_text.strip()
    all_nodes = test_obj.online_nodes_withmem
    has_huge = True if re.search('huge', out_numa_maps) else False
    N0_value = re.findall('N%s=(\d+)' % all_nodes[0], out_numa_maps)
    N1_value = re.findall('N%s=(\d+)' % all_nodes[1], out_numa_maps)
    expected_hugepage_size = test_obj.params.get('expected_hugepage_size')
    psize = expected_hugepage_size if memory_backing else memory.get_page_size() // 1024
    has_kernelpage = True if re.search('kernelpagesize_kB=%s' % psize, out_numa_maps) else False

    def _check_values(expect_huge, expect_N0, expect_N1, expect_sum=None):
        if has_huge != expect_huge:
            test_obj.test.fail("The numa_maps should %s "
                               "include 'huge' when hugepage "
                               "is '%s' used" % ('not' if not expect_huge else '',
                                                 'not' if not expect_huge else ''))
        if expect_sum:
            actual_sum = int(N0_value[0]) + int(N1_value[0])
            if actual_sum != expect_sum:
                test_obj.test.fail("Expect N%s + N%s = %d in "
                                   "numa_maps, but found '%d'" % (all_nodes[0],
                                                                  all_nodes[1],
                                                                  expect_sum,
                                                                  actual_sum))
        else:
            for node in [0, 1]:
                node_value = N0_value if node == 0 else N1_value
                value_bool = True if node_value else False
                expect_N = expect_N0 if node == 0 else expect_N1
                test_obj.test.log.debug(expect_N)
                test_obj.test.log.debug(value_bool)
                if isinstance(expect_N, bool):
                    if value_bool != expect_N:
                        test_obj.test.fail("The numa_maps should %s "
                                           "include 'N%s=', but %s "
                                           "found" % ('not' if not expect_N else '',
                                                      all_nodes[node],
                                                      'not' if not node_value else ''))
                elif node_value and int(node_value[0]) != expect_N:
                    test_obj.test.fail("Expect N%s=%d in numa_maps, "
                                       "but found '%s'" % (all_nodes[node],
                                                           expect_N,
                                                           node_value[0]))
        if not has_kernelpage:
            test_obj.test.fail("The numa_maps should "
                               "include 'kernelpagesize_kB=%s', "
                               "but not found" % psize)

    if not memory_backing:
        if single_host_node:
            _check_values(False, True, False)
    else:
        if (libvirt_version.version_compare(9, 3, 0) and
            mem_mode == 'prefered' and
                not single_host_node) or \
             (mem_mode in ['interleave', 'preferred'] and single_host_node):
            if platform.machine() == 'aarch64':
                _check_values(True, 3, 1)
            else:
                _check_values(True, 512, 500)
        elif not single_host_node and mem_mode == 'interleave':
            if platform.machine() == 'aarch64':
                _check_values(True, 2, 2)
            else:
                _check_values(True, 506, 506)
        elif not single_host_node and mem_mode in ['strict', 'restrictive']:
            _check_values(True, 0, 0, int(mem_size/psize))
    test_obj.test.log.debug("Step: verify host numa node memory allocation PASS")


def verify_cgroup(test_obj):
    """
    Verify cgroup information

    :param test_obj: NumaTest object
    """
    mem_mode = test_obj.params.get('mem_mode')
    nodeset = numa_base.convert_to_string_with_dash(test_obj.params.get('nodeset'))
    online_nodes = libvirt_numa.convert_all_nodes_to_string(test_obj.online_nodes)
    vm_pid = test_obj.vm.get_pid()
    cg = libvirt_cgroup.CgroupTest(vm_pid)
    surfix = 'emulator/cpuset.mems' if cg.is_cgroup_v2_enabled() else 'cpuset.mems'
    cpuset_mems_cgroup_path = os.path.join(cg.get_cgroup_path(controller='cpuset'), surfix)
    cmd = 'cat %s' % re.escape(cpuset_mems_cgroup_path)
    cpuset_mems = process.run(cmd, shell=True).stdout_text.strip()
    if mem_mode in ['strict', 'restrictive']:
        expected_cpuset_mems = nodeset
    elif cg.is_cgroup_v2_enabled():
        expected_cpuset_mems = ""
    else:
        expected_cpuset_mems = online_nodes
    if cpuset_mems != expected_cpuset_mems:
        test_obj.test.fail("Expect cpuset.mems in "
                           "path '%s' to be %s, but "
                           "found '%s'" % (cpuset_mems_cgroup_path,
                                           expected_cpuset_mems,
                                           cpuset_mems))
    test_obj.test.log.debug("Step: verify cgroup information PASS")


def verify_numatune(test_obj):
    """
    Verify the free memory is usable in the attached memory device

    :param test_obj: NumaTest object
    """
    mem_mode = test_obj.params.get('mem_mode')
    nodeset = test_obj.params.get('nodeset')
    test_obj.virsh_dargs.update({'ignore_status': False})
    numatune_result = virsh.numatune(test_obj.vm.name,
                                     **test_obj.virsh_dargs).stdout_text.strip()
    if not re.search(r'numa_mode\s*:\s*%s' % mem_mode, numatune_result):
        test_obj.test.fail("Expect numa_mode is '%s' in "
                           "numatune output, but not found" % mem_mode)
    if not re.search(r'numa_nodeset\s*:\s*%s' % numa_base.convert_to_string_with_dash(nodeset),
                     numatune_result):
        test_obj.test.fail("Expect numa_nodeset is '%s' "
                           "in numatune output, but not found" % nodeset)

    test_obj.test.log.debug("Step: verify numatune command PASS")


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
    verify_numatune(test_obj)
    verify_host_numa_memory_allocation(test_obj)
    verify_cgroup(test_obj)


def teardown_default(test_obj):
    """
    Default teardown function for the test

    :param test_obj: NumaTest object
    """
    test_obj.teardown()
    if test_obj.params.get('memory_backing'):
        expected_hugepage_size = test_obj.params.get('expected_hugepage_size')
        hpc = test_obj.params.get('hp_config_obj')
        if hpc:
            all_nodes = test_obj.online_nodes_withmem
            hpc.set_node_num_huge_pages('0', all_nodes[0], expected_hugepage_size)
            test_obj.test.log.debug("Get first node hugepage is "
                                    "%d", hpc.get_node_num_huge_pages(all_nodes[0],
                                                                      expected_hugepage_size))
            hpc.set_node_num_huge_pages('0', all_nodes[1], expected_hugepage_size)
            test_obj.test.log.debug("Get second node hugepage is "
                                    "%d", hpc.get_node_num_huge_pages(all_nodes[1],
                                                                      expected_hugepage_size))
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
