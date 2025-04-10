# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Nan Li <nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

import math
import os

from avocado.utils import memory
from avocado.utils import cpu

from virttest import utils_libvirtd
from virttest import virsh
from virttest import test_setup

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.staging import utils_memory
from virttest.utils_libvirt import libvirt_memory

from provider.numa import numa_base


VIRSH_ARGS = {'debug': True, 'ignore_status': False}


def check_hugepage_support(params, test):
    """
    Check if the specified hugepage size is supported on current environment

    :param params: dict, test parameters
    :param test: test object
    """
    scenario = params.get('scenario')
    if scenario in ['1G', '2M', '32M', '64K', '16G']:
        if scenario == '2M' and memory.get_huge_page_size()//1024 == 2:
            test.cancel("2M is the default huge page size and tested in another test scenario")
        vm_attrs = eval(params.get('vm_attrs', '{}'))
        if vm_attrs.get('mb'):
            numa_base.check_hugepage_availability(vm_attrs["mb"]["hugepages"]["pages"])


def adjust_parameters(params, test):
    """
    Update the test parameters according to different architecture

    :param params: dict, test parameters
    :param test: test object
    :ret: dict, the updated test parameters
    """
    def sub_update():
        set_pagesize = default_pgsize_set_mapping[default_pagesize]
        params['set_pagesize'] = set_pagesize
        params['mount_size'] = set_pagesize
        params['set_pagenum'] = int(math.floor(total_hugepage_mem/set_pagesize))

    vm_attrs = eval(params.get("vm_attrs", "{}"))
    scenario = params.get('scenario')

    current_arch = cpu.get_arch()
    default_pagesize = memory.get_page_size()//1024
    total_hugepage_mem = int(params.get('total_hugepage_mem'))
    if scenario == 'default_page_size':
        vm_attrs['mb']['hugepages']['pages'][0]['size'] = default_pagesize

    if current_arch == 'aarch64':
        # kernel page size vs. huge page size to be set for kernel mm file
        # like if kernel page size 64K, then huge page size to be set is 2048K
        default_pgsize_set_mapping = {64: 2048, 4: 1048576}
        default_hugepage_size = memory.get_huge_page_size()
        params['vm_nr_hugepages'] = int(math.floor(total_hugepage_mem/default_hugepage_size))

        if scenario == 'default_page_size':
            sub_update()
            params['HugePages_Free'] = params['vm_nr_hugepages']
            params['free_hugepages'] = params['set_pagenum']
        elif scenario in ['default_hugepage_size', 'scarce_mem']:
            sub_update()
            params['free_hugepages'] = params['set_pagenum']
        elif scenario == '1G':
            params['HugePages_Free'] = params['vm_nr_hugepages']
        elif scenario == '0':
            sub_update()
        elif scenario in ['2M', '16G', '64K', '32M']:
            params['HugePages_Free'] = params['vm_nr_hugepages']
        test.log.debug("params['HugePages_Free']=%s", params.get('HugePages_Free'))
        test.log.debug("params['free_hugepages']=%s", params.get('free_hugepages'))
        test.log.debug("params['vm_nr_hugepages']=%s", params.get('vm_nr_hugepages'))
        test.log.debug("params['set_pagesize']=%s", params.get('set_pagesize'))
        test.log.debug("params['set_pagenum']=%s", params.get('set_pagenum'))
        test.log.debug("params['mount_size']=%s", params.get('mount_size'))

    params['vm_attrs'] = vm_attrs
    test.log.debug("Updated vm_attrs:%s", vm_attrs)

    return params


def setup_test(vm_name, params, test):
    """
    Set hugepage on host

    :param vm_name: str, vm name
    :param params: dict, test parameters
    :param test: test object
    """
    test.log.info("TEST_SETUP: Set hugepage on host")
    vm_nr_hugepages = params.get("vm_nr_hugepages")
    set_pagesize = params.get("set_pagesize")
    set_pagenum = params.get("set_pagenum")
    mount_size = params.get("mount_size")

    utils_memory.set_num_huge_pages(int(vm_nr_hugepages))
    hp_cfg = test_setup.HugePageConfig(params)
    hp_cfg.set_kernel_hugepages(set_pagesize, set_pagenum, False)
    hp_cfg.hugepage_size = mount_size
    hp_cfg.mount_hugepage_fs()
    utils_libvirtd.Libvirtd().restart()


def run_test(vm, params, test):
    """
    Define guest, start guest and check mem.

    :param vm: vm instance
    :param params: dict, test parameters
    :param test: test object
    """
    define_error = params.get("define_error")
    start_error = params.get("start_error")
    save_file = params.get("save_file", "/tmp/guest.save")
    HugePages_Free = params.get("HugePages_Free")
    free_hugepages = params.get("free_hugepages")
    vm_nr_hugepages = params.get("vm_nr_hugepages")
    set_pagesize = params.get("set_pagesize")
    set_pagenum = params.get("set_pagenum")
    vm_attrs = params.get("vm_attrs")

    test.log.info("TEST_STEP1: Define guest")
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm.name)
    if vm_attrs:
        vmxml.setup_attrs(**vm_attrs)
    cmd_result = virsh.define(vmxml.xml, debug=True)
    if define_error:
        # check scenario: 0
        libvirt.check_result(cmd_result, define_error)
        return

    test.log.info("TEST_STEP2: Start guest")
    ret = virsh.start(vm.name, debug=True, ignore_error=True)
    libvirt.check_result(ret, expected_fails=start_error)
    if start_error:
        return
    vm.wait_for_login().close()
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm.name)
    test.log.debug("After start vm, get vmxml is :%s", vmxml)

    test.log.info("TEST_STEP3: Check the huge page memory usage")
    hp_cfg = test_setup.HugePageConfig(params)
    if int(utils_memory.get_num_huge_pages()) != int(vm_nr_hugepages):
        test.fail("HugePages_Total should be %s instead of %s" % (
            vm_nr_hugepages, utils_memory.get_num_huge_pages()))

    actural_nr = hp_cfg.get_kernel_hugepages(set_pagesize)
    if int(actural_nr) != int(set_pagenum):
        test.fail("nr_hugepages should be %s instead of %s" % (
            set_pagenum, actural_nr))

    if int(utils_memory.get_num_huge_pages_free()) != int(HugePages_Free):
        test.fail("HugePages_Free should be %s instead of %s" % (
            HugePages_Free, utils_memory.get_num_huge_pages_free()))

    actural_freepg = hp_cfg.get_kernel_hugepages(set_pagesize, 'free')
    if int(actural_freepg) != int(free_hugepages):
        test.fail("free_hugepages should be %s instead of %s" % (
            free_hugepages, actural_freepg))

    test.log.info("TEST_STEP4: Check the vm lifecycle with hugepage setting")
    virsh.suspend(vm.name, **VIRSH_ARGS)
    virsh.resume(vm.name, **VIRSH_ARGS)
    vm.wait_for_login().close()

    if os.path.exists(save_file):
        os.remove(save_file)
    virsh.save(vm.name, save_file, **VIRSH_ARGS)
    virsh.restore(save_file, **VIRSH_ARGS)
    vm.wait_for_login().close()

    virsh.managedsave(vm.name, **VIRSH_ARGS)
    vm.start()
    vm.wait_for_login().close()

    virsh.reboot(vm.name, **VIRSH_ARGS)
    session = vm.wait_for_login()

    test.log.info("TEST_STEP5: Verify guest memory could be consumed")
    libvirt_memory.consume_vm_freememory(session, 204800)

    virsh.destroy(vm.name, **VIRSH_ARGS)


def teardown_test(vmxml, params, test):
    """
    Clean data.

    :param vmxml: VMXML instance
    :param params: dict, test parameters
    :param test: test object
    """
    save_file = params.get("save_file", "/tmp/guest.save")

    test.log.info("TEST_TEARDOWN: Clean up env.")
    vmxml.sync()
    if os.path.exists(save_file):
        os.remove(save_file)
    hp_cfg = test_setup.HugePageConfig(params)
    hp_cfg.cleanup()


def run(test, params, env):
    """
    1.Verify different size huge page take effect for guest vm
    2.Verify error msg prompts with invalid huge page size or scarce memory

    Scenario:
    Huge page memory status: 4k, 2M, 1G, 0, scarce.
    """
    check_hugepage_support(params, test)
    vm_name = params.get("main_vm")
    params = adjust_parameters(params, test)
    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        setup_test(vm_name, params, test)
        run_test(vm, params, test)

    finally:
        teardown_test(bkxml, params, test)
