# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Red Hat
#
#   SPDX-License-Identifier: GPL-2.0
#
#   Author: Dan Zheng <dzheng@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

import os
import re

from avocado.utils import process

from virttest import libvirt_cgroup
from virttest import libvirt_version
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_memory

from provider.numa import numa_base


def setup_default(numatest_obj):
    """
    Default setup function for the test

    :param numatest_obj: NumaTest object
    """
    numatest_obj.setup(expect_node_free_mem_min=1048576)
    numatest_obj.test.log.debug("Step: setup is done")


def prepare_vm_xml(numatest_obj):
    """
    Customize the vm xml

    :param numatest_obj: NumaTest object

    :return: VMXML object updated
    """
    numa_memnode = numatest_obj.params.get('numa_memnode')
    numa_memnode = numa_memnode % numatest_obj.online_nodes_withmem[1]
    numatest_obj.params['numa_memnode'] = numa_memnode
    vmxml = numatest_obj.prepare_vm_xml()
    numatest_obj.test.log.debug("Step: vm xml before defining:\n%s", vmxml)
    return vmxml


def generate_start_expected_error(numatest_obj):
    """
    produce the expected error message

    :param numatest_obj: NumaTest object

    :return: str, error message to be checked
    """
    err_msg = numatest_obj.produce_expected_error()
    mem_mode = numatest_obj.params.get('mem_mode')
    single_host_node = numatest_obj.params.get('single_host_node')
    memnode_mode = numatest_obj.params.get('memnode_mode')

    if (not libvirt_version.version_compare(9, 4, 0) and
       memnode_mode != 'restrictive' and
       single_host_node == 'yes' and
       mem_mode == 'strict'):
        new_err_msg = "cannot bind memory to host NUMA nodes: Invalid argument"
        err_msg = "%s|%s" % (err_msg, new_err_msg) if err_msg else new_err_msg
    return err_msg


def generate_define_expected_error(numatest_obj):
    """
    produce the expected error message

    :param numatest_obj: NumaTest object

    :return: str, error message to be checked
    """
    mem_mode = numatest_obj.params.get('mem_mode')
    single_host_node = numatest_obj.params.get('single_host_node')
    memnode_mode = numatest_obj.params.get('memnode_mode')
    err_msg = ''
    msg = "'restrictive' mode is required in %s element when mode is 'restrictive' in %s element"
    if mem_mode == 'restrictive' and memnode_mode != 'restrictive':
        err_msg = msg % ('memnode', 'memory')
    if (libvirt_version.version_compare(9, 4, 0) and
       (mem_mode != 'restrictive' or single_host_node is None) and
       memnode_mode == 'restrictive'):
        err_msg = msg % ('memory', 'memnode')

    return err_msg


def check_vm_numa_1_memory_allocation(numatest_obj, mem_size):
    """
    Check vm numa node 1's memory allocation information

    :param numatest_obj: NumaTest object
    :param mem_size: int, vm node memory size
    """
    out_numa_maps = numa_base.get_host_numa_memory_alloc_info(mem_size)
    memnode_mode = numatest_obj.params.get('memnode_mode')
    mem_mode = numatest_obj.params.get('mem_mode')
    single_host_node = numatest_obj.params.get('single_host_node')
    all_nodes = numatest_obj.online_nodes_withmem
    node_values = {}
    numatest_obj.test.log.debug("all_nodes:%s", all_nodes)
    for node_num in all_nodes:
        node_value = re.findall('N%s=(\d+)' % node_num, out_numa_maps)
        if not node_value:
            continue
        numatest_obj.test.log.debug("N%s_value:%s", node_num, node_value[0])
        node_values[node_num] = node_value[0]

    def _check_N0_no_N1():
        if all_nodes[0] not in node_values:
            numatest_obj.test.fail("Expect N%s > 0, but found not" % all_nodes[0])
        if all_nodes[1] in node_values:
            numatest_obj.test.fail("Not expect N%s to exist, but found" % all_nodes[1])

    def _check_N0_N1_sum():
        n0_value = 0 if all_nodes[0] not in node_values else int(node_values[all_nodes[0]])
        n1_value = 0 if all_nodes[1] not in node_values else int(node_values[all_nodes[1]])
        if n0_value + n1_value <= 0:
            numatest_obj.test.fail("Expect N%s + N%s > 0, but found "
                                   "not" % (all_nodes[0], all_nodes[1]))

    def _check_all_nodes_sum():
        node_value_sum = 0
        for node_value in node_values.values():
            node_value_sum += int(node_value)
        if node_value_sum <= 0:
            numatest_obj.test.fail("Expect the sum of all node values is greater than 0, but found not")

    def _check_N0():
        if all_nodes[0] not in node_values:
            numatest_obj.test.fail("Expect N%s > 0, but found not" % all_nodes[0])

    if mem_mode is not None:
        if single_host_node == "yes":
            if mem_mode == 'strict' and memnode_mode in ['strict', 'interleave', 'preferred']:
                pat = "bind:%s" % all_nodes[0]
                _check_N0_no_N1()
            if mem_mode == 'strict' and memnode_mode == 'restrictive':
                pat = "bind:%s" % all_nodes[0]
                _check_N0_N1_sum()
            if mem_mode == 'interleave':
                pat = "interleave:%s" % all_nodes[0]
                _check_N0()
            if mem_mode == 'preferred':
                pat = "prefer \(many\):%s" % all_nodes[0] \
                    if libvirt_version.version_compare(9, 3, 0) \
                    else "prefer:%s" % all_nodes[0]
                _check_N0()
            if mem_mode == 'restrictive':
                pat = "default"
                _check_N0_N1_sum()
        elif single_host_node == 'no':
            nodeset = numa_base.convert_to_string_with_dash(numatest_obj.params['nodeset'])
            if mem_mode == 'strict':
                pat = "bind:%s" % nodeset
            if mem_mode == 'interleave':
                pat = "interleave:%s" % nodeset
            if mem_mode == 'preferred':
                pat = "prefer \(many\):%s" % nodeset
            if mem_mode == 'restrictive':
                pat = "default"
            _check_N0_N1_sum()
    else:
        pat = "default"
        _check_all_nodes_sum()

    search_pattern = r"\s+%s\s+anon=" % pat
    if not re.findall(search_pattern, out_numa_maps):
        numatest_obj.test.fail("Fail to find the pattern '%s' in "
                               "numa_maps output '%s'" % (search_pattern,
                                                          out_numa_maps))
    numatest_obj.test.log.debug("Step: verify vm numa node 1 memory allocation - PASS")


def check_vm_numa_0_memory_allocation(numatest_obj, mem_size):
    """
    Check vm numa node 0's memory allocation information

    :param numatest_obj: NumaTest object
    :param mem_size: int, vm node memory size
    """
    out_numa_maps = numa_base.get_host_numa_memory_alloc_info(mem_size)
    all_nodes = numatest_obj.online_nodes_withmem
    node0_value = re.findall('N%s=(\d+)' % all_nodes[0], out_numa_maps)
    node1_value = re.findall('N%s=(\d+)' % all_nodes[1], out_numa_maps)
    memnode_mode = numatest_obj.params.get('memnode_mode')

    if memnode_mode == 'strict':
        pat = "bind:%s" % all_nodes[1]
    elif memnode_mode == 'interleave':
        pat = "interleave:%s" % all_nodes[1]
    elif memnode_mode == 'preferred':
        if not libvirt_version.version_compare(9, 3, 0):
            pat = "prefer:%s" % all_nodes[1]
        else:
            pat = "prefer \(many\):%s" % all_nodes[1]
    else:
        pat = "default"
        node0_value = 0 if not node0_value else int(node0_value[0])
        node1_value = 0 if not node1_value else int(node1_value[0])
        if node0_value + node1_value <= 0:
            numatest_obj.test.fail("Expect N%s + N%s > 0, but "
                                   "found not" % (all_nodes[0], all_nodes[1]))
    search_pattern = r"\s+%s\s+anon=" % pat
    if not re.findall(search_pattern, out_numa_maps):
        numatest_obj.test.fail("Fail to find the pattern '%s' in "
                               "numa_maps output '%s'" % (search_pattern,
                                                          out_numa_maps))
    numatest_obj.test.log.debug("Step: verify vm numa 0 memory allocation - PASS")


def check_cgroup(numatest_obj):
    """
    Check vm cgroup information

    :param numatest_obj: NumaTest object
    """
    memnode_mode = numatest_obj.params.get('memnode_mode')
    mem_mode = numatest_obj.params.get('mem_mode')
    single_host_node = numatest_obj.params.get('single_host_node')
    all_nodes = numatest_obj.online_nodes_withmem

    vm_pid = numatest_obj.vm.get_pid()
    cg = libvirt_cgroup.CgroupTest(vm_pid)
    is_cgroup2 = cg.is_cgroup_v2_enabled()
    surfix_emulator = 'emulator/cpuset.mems' if is_cgroup2 else 'cpuset.mems'
    surfix_vcpu0 = 'vcpu0/cpuset.mems' if is_cgroup2 else '../vcpu0/cpuset.mems'
    surfix_vcpu1 = 'vcpu1/cpuset.mems' if is_cgroup2 else '../vcpu1/cpuset.mems'
    numatest_obj.test.log.debug("The cgroup_path for "
                                "controller 'cpuset':%s", cg.get_cgroup_path(controller='cpuset'))
    emulator_cpuset_mems_cgroup_path = os.path.join(cg.get_cgroup_path(controller='cpuset'),
                                                    surfix_emulator)
    vcpu0_cpuset_mems_cgroup_path = os.path.join(cg.get_cgroup_path(controller='cpuset'),
                                                 surfix_vcpu0)
    vcpu1_cpuset_mems_cgroup_path = os.path.join(cg.get_cgroup_path(controller='cpuset'),
                                                 surfix_vcpu1)
    cmd_emulator = 'cat %s' % re.escape(emulator_cpuset_mems_cgroup_path)
    cmd_vcpu0 = 'cat %s' % re.escape(vcpu0_cpuset_mems_cgroup_path)
    cmd_vcpu1 = 'cat %s' % re.escape(vcpu1_cpuset_mems_cgroup_path)
    emulator_cpuset_mems = process.run(cmd_emulator, shell=True).stdout_text.strip()
    vcpu0_cpuset_mems = process.run(cmd_vcpu0, shell=True).stdout_text.strip()
    vcpu1_cpuset_mems = process.run(cmd_vcpu1, shell=True).stdout_text.strip()
    nodeset_memnode = eval(numatest_obj.params['numa_memnode'])[0].get('nodeset')
    if (single_host_node == 'yes' and mem_mode in ['strict', 'restrictive']
       and memnode_mode == 'restrictive'):
        if (emulator_cpuset_mems != str(all_nodes[0]) or
           vcpu1_cpuset_mems != str(all_nodes[0])):
            numatest_obj.test.fail("Expect emulator/cpuset.mems and "
                                   "vcpu1/cpuset.mems to be %s, but "
                                   "found %s and %s" % (all_nodes[0],
                                                        emulator_cpuset_mems,
                                                        vcpu1_cpuset_mems))
        if vcpu0_cpuset_mems != nodeset_memnode:
            numatest_obj.test.fail("Expect vcpu0/cpuset.mems to "
                                   "be %s, but found %s" % (nodeset_memnode,
                                                            vcpu0_cpuset_mems))
    if mem_mode in ['interleave', 'preferred'] and memnode_mode == 'restrictive':
        for item in [emulator_cpuset_mems, vcpu1_cpuset_mems]:
            if item != '':
                numatest_obj.test.fail("Expect cpuset.mems to be '', "
                                       "but found %s" % item)
        if vcpu0_cpuset_mems != nodeset_memnode:
            numatest_obj.test.fail("Expect vcpu0/cpuset.mems to be %s, "
                                   "but found %s" % (nodeset_memnode,
                                                     vcpu0_cpuset_mems))
    if ((not mem_mode or mem_mode in ['interleave', 'preferred']) and
       memnode_mode == 'strict'):
        if is_cgroup2:
            if any([emulator_cpuset_mems, vcpu0_cpuset_mems, vcpu1_cpuset_mems]):
                numatest_obj.test.fail("Expect emulator/cpuset.mems and "
                                       "vcpu0/cpuset.mems and vcpu1/cpuset.mems "
                                       "to be '', but found "
                                       "%s %s %s" % (emulator_cpuset_mems,
                                                     vcpu0_cpuset_mems,
                                                     vcpu1_cpuset_mems))
        else:
            numatest_obj.test.log.debug("emulator_cpuset_mems=%s, "
                                        "vcpu0_cpuset_mems=%s, "
                                        "vcpu1_cpuset_mems=%s", emulator_cpuset_mems,
                                        vcpu0_cpuset_mems, vcpu1_cpuset_mems)
            all_nodes_str = ['%s' % a_node for a_node in all_nodes]
            nodeset = numa_base.convert_to_string_with_dash(','.join(all_nodes_str))
            for item in [emulator_cpuset_mems, vcpu0_cpuset_mems, vcpu1_cpuset_mems]:
                if item != nodeset:
                    numatest_obj.test.fail("Expect cpuset.mems to be %s, "
                                           "but found %s" % (nodeset, item))
    if (single_host_node == 'yes' and mem_mode == 'strict' and
       memnode_mode != 'restrictive'):
        mem_nodeset = numatest_obj.params['nodeset']
        memnode_nodeset = eval(numatest_obj.params['numa_memnode'])[0].get('nodeset')
        nodeset = ','.join(sorted(set(mem_nodeset).union(set(memnode_nodeset))))
        nodeset = numa_base.convert_to_string_with_dash(nodeset)
        for item in [emulator_cpuset_mems, vcpu0_cpuset_mems, vcpu1_cpuset_mems]:
            if item != nodeset:
                numatest_obj.test.fail("Expect cpuset.mems to be %s, "
                                       "but found %s" % (nodeset, item))
    if (single_host_node == 'no' and mem_mode == 'strict' and
       memnode_mode != 'restrictive'):
        nodeset = numa_base.convert_to_string_with_dash(numatest_obj.params['nodeset'])
        for item in [emulator_cpuset_mems, vcpu0_cpuset_mems, vcpu1_cpuset_mems]:
            if item != nodeset:
                numatest_obj.test.fail("Expect cpuset.mems to be %s, "
                                       "but found %s" % (nodeset, item))
    if (single_host_node == 'no' and mem_mode in ['strict', 'restrictive'] and
       memnode_mode == 'restrictive'):
        nodeset = numa_base.convert_to_string_with_dash(numatest_obj.params['nodeset'])
        for item in [emulator_cpuset_mems, vcpu1_cpuset_mems]:
            if item != nodeset:
                numatest_obj.test.fail("Expect cpuset.mems to be %s, "
                                       "but found %s" % (nodeset, item))
        if vcpu0_cpuset_mems != nodeset_memnode:
            numatest_obj.test.fail("Expect vcpu0/cpuset.mems to be %s, but "
                                   "found %s" % (nodeset_memnode,
                                                 vcpu0_cpuset_mems))
    numatest_obj.test.log.debug("Step: verify vm cgroup - PASS")


def run_default(numatest_obj):
    """
    Default run function for the test

    :param numatest_obj: NumaTest object
    """
    numatest_obj.test.log.debug("Step: prepare vm xml")
    vmxml = prepare_vm_xml(numatest_obj)
    numatest_obj.test.log.debug("Step: define vm and check results")
    numatest_obj.virsh_dargs.update({'ignore_status': True})
    ret = virsh.define(vmxml.xml, **numatest_obj.virsh_dargs)
    err_msg_expected = generate_define_expected_error(numatest_obj)
    libvirt.check_result(ret, expected_fails=err_msg_expected)
    if err_msg_expected:
        return
    numatest_obj.test.log.debug("Step: start vm and check results")
    ret = virsh.start(numatest_obj.vm.name, **numatest_obj.virsh_dargs)
    err_msg_expected = generate_start_expected_error(numatest_obj)
    libvirt.check_result(ret, expected_fails=err_msg_expected)
    if err_msg_expected:
        return
    numatest_obj.test.log.debug("After vm is started, vm xml:\n"
                                "%s", vm_xml.VMXML.new_from_dumpxml(numatest_obj.vm.name))
    vm_session = numatest_obj.vm.wait_for_login()
    libvirt_memory.consume_vm_freememory(vm_session)
    vm_session.close()
    numatest_obj.test.log.debug("Step: consume vm memory - PASS")
    check_vm_numa_0_memory_allocation(numatest_obj, numatest_obj.params.get('vm_node0_mem'))
    check_vm_numa_1_memory_allocation(numatest_obj, numatest_obj.params.get('vm_node1_mem'))
    check_cgroup(numatest_obj)


def teardown_default(numatest_obj):
    """
    Default teardown function for the test

    :param numatest_obj: NumaTest object
    """
    numatest_obj.teardown()

    numatest_obj.test.log.debug("Step: teardown is done")


def run(test, params, env):
    """
    Test mixed host and guest numa memory binding
    """
    libvirt_version.is_libvirt_feature_supported(params)
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    numatest_obj = numa_base.NumaTest(vm, params, test)
    try:
        setup_default(numatest_obj)
        run_default(numatest_obj)
    finally:
        teardown_default(numatest_obj)
