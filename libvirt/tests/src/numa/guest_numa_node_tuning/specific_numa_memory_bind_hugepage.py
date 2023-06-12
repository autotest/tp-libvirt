#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Red Hat
#
#   SPDX-License-Identifier: GPL-2.0
#   Author: Dan Zheng <dzheng@redhat.com>
#

import re

from virttest import utils_libvirtd
from virttest import virsh
from virttest import test_setup
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
    hpc.setup()
    utils_libvirtd.Libvirtd().restart()
    test_obj.params['hp_config_2M'] = hpc
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
    return test_obj.produce_expected_error()


def verify_hugepage_memory_in_numa_maps(test_obj):
    """
    Verify hugepage memory in numa_maps is correct

    :param test_obj: NumaTest object
    """
    out_numa_maps = numa_base.get_host_numa_memory_alloc_info(test_obj.params.get('vm_node0_mem'))
    mem_mode = test_obj.params.get('mem_mode')
    nodeset = test_obj.params.get('nodeset')
    target_hugepages = test_obj.params.get('target_hugepages')
    if mem_mode == 'strict':
        mem_mode_match = 'bind:%s' % nodeset
    elif mem_mode == 'interleave':
        mem_mode_match = 'interleave:%s' % nodeset
    elif mem_mode == 'preferred':
        mem_mode_match = 'prefer.*:%s' % nodeset
    else:
        mem_mode_match = 'default'
    pat = r"%s.*huge anon=\d+.*N%s=%s kernelpagesize_kB" % (mem_mode_match,
                                                            nodeset,
                                                            target_hugepages)
    if not re.search(pat, out_numa_maps):
        test_obj.test.fail("Fail to find the pattern '%s' in "
                           "numa_maps output '%s'" % (pat,
                                                      out_numa_maps))
    test_obj.test.log.debug("Step: verify hugepage memory in numa_maps: PASS")


def run_default(test_obj):
    """
    Default run function for the test

    :param test_obj: NumaTest object
    """
    test_obj.test.log.debug("Step: prepare vm xml")
    vmxml = prepare_vm_xml(test_obj)
    test_obj.test.log.debug("Step: define vm")
    virsh.define(vmxml.xml, **test_obj.virsh_dargs)
    test_obj.test.log.debug("Step: start vm and check result")
    test_obj.virsh_dargs.update({'ignore_status': True})
    ret = virsh.start(test_obj.vm.name, **test_obj.virsh_dargs)
    err_msg_expected = produce_expected_error(test_obj)
    libvirt.check_result(ret, expected_fails=err_msg_expected)
    #  Some negative cases will fail to define the vm, so they will return
    #  to skip following steps
    if err_msg_expected:
        return
    test_obj.test.log.debug("After vm is started, vm xml:\n"
                            "%s", vm_xml.VMXML.new_from_dumpxml(test_obj.vm.name))
    test_obj.vm.wait_for_login()
    verify_hugepage_memory_in_numa_maps(test_obj)


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
    try:
        setup_default(numatest_obj)
        run_default(numatest_obj)
    finally:
        teardown_default(numatest_obj)
