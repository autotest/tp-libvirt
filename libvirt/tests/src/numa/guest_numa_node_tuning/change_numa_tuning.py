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

from avocado.utils import process

from virttest import libvirt_cgroup
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_memory
from virttest.utils_libvirt import libvirt_misc
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt


from provider.numa import numa_base


def setup_default(test_obj):
    """
    Default setup function for the test

    :param test_obj: NumaTest object
    """
    test_obj.setup()
    test_obj.test.log.debug("Step: setup is done")


def prepare_vm_xml(test_obj):
    """
    Customize the vm xml

    :param test_obj: NumaTest object

    :return: VMXML object updated
    """
    vmxml = test_obj.prepare_vm_xml()
    all_nodes = test_obj.all_usable_numa_nodes
    mem_total = test_obj.host_numa_info.read_from_node_meminfo(all_nodes[0], 'MemTotal')
    offset = 1000000
    if int(mem_total) < offset:
        test_obj.test.error("Expect Total memory in node %s bigger than %s, "
                            "but found %s" % (all_nodes[0], offset, int(mem_total)))
    vmxml.memory = int(mem_total) + offset
    vmxml.current_mem = max(int(mem_total) - offset, 1536)
    test_obj.test.log.debug("Step: vm xml before defining:\n%s", vmxml)
    vmxml.sync()
    return vmxml


def produce_expected_error(test_obj):
    """
    produce the expected error message

    :param test_obj: NumaTest object

    :return: str, error message to be checked
    """
    err_msg = test_obj.produce_expected_error()
    return err_msg


def verify_host_numa_memory_allocation(test_obj, check_N0=False):
    """
    Verify the host numa node memory is allocated as requested by vm

    :param test_obj: NumaTest object
    """
    mem_size = get_memory_in_vmxml(test_obj)
    out_numa_maps = numa_base.get_host_numa_memory_alloc_info(mem_size)
    all_nodes = test_obj.all_usable_numa_nodes
    N0_value = re.findall('N%s=(\d+)' % all_nodes[0], out_numa_maps)
    N1_value = re.findall('N%s=(\d+)' % all_nodes[1], out_numa_maps)
    has_kernelpage = bool(re.search('kernelpagesize_kB=4', out_numa_maps))
    if not has_kernelpage:
        test_obj.test.fail("The numa_maps should "
                           "include 'kernelpagesize_kB=4'， but not found")
    if not N1_value:
        test_obj.test.fail("The numa_maps should "
                           "include 'N%s=', but not found" % all_nodes[1])
    if check_N0 and not N0_value:
        test_obj.test.fail("The numa_maps should "
                           "include 'N%s=', but not found" % all_nodes[0])
    test_obj.test.log.debug("Step: verify host numa node memory allocation PASS")


def verify_cgroup(test_obj, nodeset):
    """
    Verify cgroup information

    :param test_obj: NumaTest object
    :param nodeset: str, numa node set
    """
    vm_pid = test_obj.vm.get_pid()
    cg = libvirt_cgroup.CgroupTest(vm_pid)
    surfix = 'emulator/cpuset.mems' if cg.is_cgroup_v2_enabled() else 'cpuset.mems'
    cpuset_mems_cgroup_path = os.path.join(cg.get_cgroup_path(controller='cpuset'), surfix)
    cmd = 'cat %s' % re.escape(cpuset_mems_cgroup_path)
    cpuset_mems = process.run(cmd, shell=True).stdout_text.strip()
    if cpuset_mems != nodeset:
        test_obj.test.fail("Expect cpuset.mems in "
                           "path '%s' to be %s, but "
                           "found '%s'" % (cpuset_mems_cgroup_path,
                                           nodeset,
                                           cpuset_mems))

    test_obj.test.log.debug("Step: verify cgroup information PASS")


def select_different_mem_mode(current_mem_mode):
    """
    Select another different numa memory mode

    :param current_mem_mode: str, numa memory mode
    :return: str, numa memory mode selected
    """
    mem_modes = ['strict', 'interleave', 'preferred', 'restrictive']
    return [mode for mode in mem_modes if mode != current_mem_mode][0]


def verify_numatune(test_obj, mem_mode, nodeset, error_expected=None):
    """
    Verify the free memory is usable in the attached memory device

    :param test_obj: NumaTest object
    :param mem_mode: str, numa memory mode
    :param nodeset: str, numa node set
    :param error_expected: str, expected error messages
    """
    test_obj.virsh_dargs.update({'ignore_status': True})
    numatune_result = virsh.numatune(test_obj.vm.name,
                                     mode=mem_mode,
                                     nodeset=nodeset,
                                     **test_obj.virsh_dargs)
    libvirt.check_result(numatune_result, expected_fails=error_expected)
    if not error_expected:
        vmxml = vm_xml.VMXML.new_from_dumpxml(test_obj.vm.name)
        xpaths_text = [{'element_attrs': ["./numatune/memory[@mode='%s']" % mem_mode,
                                          "./numatune/memory[@nodeset='%s']" % nodeset]}]
        libvirt_vmxml.check_guest_xml_by_xpaths(vmxml, xpaths_text)
        numatune_result = virsh.numatune(test_obj.vm.name,
                                         **test_obj.virsh_dargs).stdout_text.strip()
        ret = libvirt_misc.convert_to_dict(numatune_result, r'(\S+)\s+:\s+(\S+)')
        if ret['numa_mode'] != mem_mode or ret['numa_nodeset'] != str(nodeset):
            test_obj.test.fail("Expect numa_mode=%s, numa_nodeset=%s, "
                               "but found %s" % (mem_mode, nodeset, ret))
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
    test_obj.test.log.debug("Step: change numatune mode and nodeset on shutoff vm")
    mem_mode = test_obj.params.get('mem_mode')
    another_mem_mode = select_different_mem_mode(mem_mode)
    verify_numatune(test_obj,
                    mem_mode=another_mem_mode,
                    nodeset=str(test_obj.all_usable_numa_nodes[1]))
    numatune_result = virsh.numatune(test_obj.vm.name,
                                     mode=mem_mode,
                                     nodeset=str(test_obj.all_usable_numa_nodes[0]),
                                     **test_obj.virsh_dargs).stdout_text.strip()
    test_obj.test.log.debug("Step: start vm")
    ret = virsh.start(test_obj.vm.name, **test_obj.virsh_dargs)
    test_obj.test.log.debug("After vm is started, vm xml:\n"
                            "%s", vm_xml.VMXML.new_from_dumpxml(test_obj.vm.name))
    test_obj.vm.wait_for_login().close()
    test_obj.test.log.debug("Step: change numatune mode on running vm")
    error_expected = produce_expected_error(test_obj)
    verify_numatune(test_obj,
                    mem_mode=another_mem_mode,
                    nodeset=test_obj.all_usable_numa_nodes[0],
                    error_expected=error_expected)


def get_memory_in_vmxml(test_obj):
    """
    Get the memory setting in vm xml

    :param test_obj:  NumaTest object
    :return: int, memory setting in vm xml
    """
    vmxml = vm_xml.VMXML.new_from_dumpxml(test_obj.vm.name)
    return vmxml.memory


def run_mem_mode_restrictive(test_obj):
    """
    Test function for numa memory mode is restrictive

    :param test_obj: NumaTest object
    """
    mem_mode = test_obj.params.get('mem_mode')
    run_default(test_obj)
    test_obj.test.log.debug("Step: change numatune nodeset on running vm when mode is restrictive")
    verify_numatune(test_obj, mem_mode=mem_mode, nodeset=test_obj.all_usable_numa_nodes[1])
    verify_host_numa_memory_allocation(test_obj)
    nodeset = "%s,%s" % (test_obj.all_usable_numa_nodes[0], test_obj.all_usable_numa_nodes[1])
    nodeset = numa_base.convert_to_string_with_dash(nodeset)
    test_obj.test.log.debug("Step: change numatune nodeset in range on running vm when mode is restrictive")
    verify_numatune(test_obj, mem_mode=mem_mode, nodeset=nodeset)
    virsh.setmem(test_obj.vm.name, get_memory_in_vmxml(test_obj))
    session = test_obj.vm.wait_for_login()
    test_obj.test.log.debug("Step: consume vm free memory")
    libvirt_memory.consume_vm_freememory(session)
    verify_host_numa_memory_allocation(test_obj, check_N0=True)
    verify_cgroup(test_obj, nodeset)


def teardown_default(test_obj):
    """
    Default teardown function for the test

    :param test_obj: NumaTest object
    """
    test_obj.teardown()
    test_obj.test.log.debug("Step: teardown is done")


def run(test, params, env):
    """
    Test for numa memory binding with emulator thread pin
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    numatest_obj = numa_base.NumaTest(vm, params, test)
    memory_binding_mode = params.get("memory_binding_mode")
    run_test = eval("run_%s" % memory_binding_mode) if "run_%s" % memory_binding_mode in globals() else run_default
    try:
        setup_default(numatest_obj)
        run_test(numatest_obj)
    finally:
        teardown_default(numatest_obj)
