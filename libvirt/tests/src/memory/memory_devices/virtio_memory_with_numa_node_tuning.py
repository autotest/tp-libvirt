# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Nannan Li <nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import copy
import re

from avocado.utils import memory
from avocado.utils import process

from virttest import virsh
from virttest import test_setup
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirtd import Libvirtd
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_libvirt import libvirt_memory

from provider.memory import memory_base
from provider.numa import numa_base

virsh_dargs = {"ignore_status": False, "debug": True}
default_hugepage_size = memory.get_huge_page_size()


def get_two_hugepage_sizes(test, params):
    """
    Get two huge page sizes

    :param test: test object
    :param params: Dictionary with the test parameters
    :return default_hugepage_size, current default hugepage size value
    max(hugepage_sizes), the max hugepage size that is closed to upper_limit and
    different from default hugepage size.
    """
    upper_limit = params.get("upper_limit", 1048576)

    hpc = test_setup.HugePageConfig(params)
    supported_hugepage_size = hpc.get_multi_supported_hugepage_size()
    supported_hugepage_size.remove(str(default_hugepage_size))

    hugepage_sizes = []
    for size in supported_hugepage_size:
        if upper_limit % int(size) == 0:
            hugepage_sizes.append(int(size))

    test.log.debug("Get default huge page size:%s, another huge page size %s",
                   default_hugepage_size, max(hugepage_sizes))

    return default_hugepage_size, max(hugepage_sizes)


def _allocate_huge_memory(params, test, allocate_mem,
                          hugepage_size, hugepage_path=None):
    """
    Allocate hugepage memory.

    :param params: Dictionary with the test parameters
    :param test: test object.
    :param allocate_mem, total allocate memory on each numa node.
    :param hugepage_size: the hugepage size to allocate.
    :param hugepage_path: the hugepage path to allocate.
    """
    kernel_hp_file = params.get("kernel_hp_tmpl_file")
    cleanup_file = params.get("cleanup_file", [])

    target_nodes = params.get("numa_obj").online_nodes_withmem
    params.update({"all_nodes": target_nodes})

    test.log.debug("Allocate %sKiB on %s pagesize", allocate_mem, hugepage_size)
    params.update({'target_nodes': ' '.join(str(n) for n in target_nodes)})
    for node in target_nodes:
        params.update({
            'target_num_node%s' % node: allocate_mem / hugepage_size})
        params.update(
            {"kernel_hp_file": kernel_hp_file % (node, hugepage_size)})
        cleanup_file.append(params.get("kernel_hp_file"))
    params.update({"cleanup_file": cleanup_file})

    hpc = test_setup.HugePageConfig(params)
    hpc.hugepage_size = hugepage_size
    if hugepage_path:
        hpc.hugepage_path = hugepage_path % hugepage_size
        params.update({"hg_path": hpc.hugepage_path})
    else:
        # Give a always mounted path to avoid mount in HugePageConfig
        hpc.hugepage_path = '/'
    hpc.setup()
    Libvirtd().restart()


def allocate_huge_memory(params, test):
    """
    Allocate hugepage memory with two hugepage sizes on each numa node.

    :param params: instance of avocado params class
    :param test: test object.
    """
    hugepage_path = params.get("hugepage_path")
    default_hp_size = params.get("default_hp_size")
    another_hp_size = params.get("another_hp_size")
    allocate_huge_mem = int(re.findall(r"\d+", params.get("allocate_huge_mem"))[0])
    allocate_huge_mem_1 = int(re.findall(r"\d+", params.get("allocate_huge_mem_1"))[0])

    _allocate_huge_memory(
        params, test, allocate_huge_mem, default_hp_size)
    _allocate_huge_memory(
        params, test, allocate_huge_mem_1, another_hp_size, hugepage_path)

    params.update(
        {"allocate_mem_and_hp_match": {default_hp_size: allocate_huge_mem,
                                       another_hp_size: allocate_huge_mem_1}})


def create_vm_attrs(params):
    """
    Create vm defined attrs.

    :param params: instance of avocado params class
    """
    vm_attrs = params.get('vm_attrs', '{}')
    another_hp_size = params.get("another_hp_size")
    with_numa_tuning = params.get("numa_tuning") != "undefined"

    if with_numa_tuning:
        all_nodes = params.get('all_nodes')
        define_attrs = eval(
            vm_attrs % (another_hp_size, all_nodes[0], all_nodes[1]))
    else:
        define_attrs = eval(vm_attrs % another_hp_size)

    return define_attrs


def create_mem_objects(params):
    """
    create memory objects list.

    :param params: instance of avocado params class
    :return mem_obj: memory objects list.
    """
    case = params.get("case")
    virtio_mem_list = params.get("virtio_mem_list")
    default_hp_size = params.get("default_hp_size")
    another_hp_size = params.get("another_hp_size")
    block_size = max(default_hp_size, another_hp_size)

    if case == "with_source_virtio_mem":
        virtio_mem_list = virtio_mem_list % (
            default_hp_size, default_hp_size, another_hp_size, block_size)
    elif case == "no_source_virtio_mem":
        virtio_mem_list = virtio_mem_list % (default_hp_size, block_size)
    elif case == "requested_bigger_than_host_numa":
        virtio_mem_list = virtio_mem_list % block_size

    mem_obj = []
    for mem in eval(virtio_mem_list):
        obj = libvirt_vmxml.create_vm_device_by_type("memory", mem)
        mem_obj.append(obj)

    return mem_obj


def plug_virtio_mem(params, vm, operation, mem_obj_list):
    """
    Hot plug or Cold plug memory

    :param params: dictionary with the test parameters.
    :param vm: vm object.
    :param operation: flag for hot plugging or cold plugging.
    :param mem_obj_list: memory device object list to plug.
    """
    vm_name = params.get("main_vm")
    attach_option = params.get("attach_option")

    if operation == "cold_plug":
        pass
    elif operation == "hot_plug":
        vm.start()
        vm.wait_for_login().close()

    for mem in mem_obj_list:
        virsh.attach_device(vm_name, mem.xml, flagstr=attach_option,
                            **virsh_dargs)


def consume_guest_mem(vm, test):
    """
    Consume guest memory

    :param vm, vm object
    :param test: test instance.
    """
    if not vm.is_alive():
        vm.start()

    session = vm.wait_for_login()
    status, output = libvirt_memory.consume_vm_freememory(session)
    if status:
        test.fail("Fail to consume guest memory. Got error:%s" % output)
    session.close()


def check_numa_memory_allocation(params, test, dest_size):
    """
    Check the numa node memory.

    :param params: dictionary with the test parameters.
    :param test: test instance
    :param dest_size: target size of virtio mem device
    """
    all_nodes = params.get('all_nodes')

    numa_maps = numa_base.get_host_numa_memory_alloc_info(dest_size)
    N0_value = re.findall(r'N%s=(\d+)' % all_nodes[0], numa_maps)
    N1_value = re.findall(r'N%s=(\d+)' % all_nodes[1], numa_maps)
    if not N0_value:
        test.fail(
            "The numa_maps should include 'N%s=', but not found" % all_nodes[0])
    if N1_value:
        test.fail(
            "The numa_maps should not include 'N%s=', but found" % all_nodes[1])


def _get_freepages(node, pagesize):
    """
    Get virsh freepages for each node and pagesize

    :param node: host numa node value.
    :param pagesize: hugepage size value.
    :return free pages number
    """
    res = virsh.freepages(node, pagesize, debug=True).stdout_text
    return int(res.split()[-1])


def _get_another_node(params, node):
    """
    Get another numa node except current node

    :param params: dictionary with the test parameters.
    :param node: current node.
    :return another numa node except current node.
    """
    all_nodes = params.get("all_nodes")
    nodes = copy.deepcopy(all_nodes)
    nodes.remove(node)

    return nodes[0]


def _get_left_mem(vm, params, test, host_numa_node, hp_size):
    """
    Get specific host numa node left memory value for specific hugepage size

    :param vm: vm object
    :param params: dict wrapped with params
    :param test: test object
    :param host_numa_node: host numa node
    :param hp_size: hugepage size
    """
    with_numa_tuning = params.get("numa_tuning") != "undefined"
    match = params.get("allocate_mem_and_hp_match")
    each_node_total_mem = match[hp_size]
    used_mem = 0
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm.name)

    # Get memory backing hugepage size
    mb_pagesize = int(vmxml.mb.hugepages.pages[0]['size'])
    mb_guest_node = int(vmxml.mb.hugepages.pages[0]['nodeset'])

    # Calculate virtio memory used memory.
    for mem in vmxml.devices.by_device_tag('memory'):
        requested = mem.target.requested_size
        mem_attrs = mem.fetch_attrs()
        mem_source = mem_attrs.get("source")
        if mem_source:
            used_host_numa_node = int(mem_source['nodemask'])
            used_pagesize = int(mem_source['pagesize'])
        else:
            guest_node = int(mem_attrs['target']['node'])
            used_pagesize = mb_pagesize
            used_host_numa_node = ''
            if guest_node == mb_guest_node:
                if with_numa_tuning:
                    used_host_numa_node = _get_another_node(params, guest_node)

                else:
                    used_host_numa_node = guest_node

        if host_numa_node == used_host_numa_node and used_pagesize == hp_size:
            used_mem += requested

    # Calculate numa topology used memory.
    if with_numa_tuning:
        used_host_numa_node = _get_another_node(params, mb_guest_node)
    else:
        used_host_numa_node = mb_guest_node
    if host_numa_node == used_host_numa_node and mb_pagesize == hp_size:
        for cell in vmxml.cpu.numa_cell:
            if int(cell.id) == mb_guest_node:
                used_mem += int(cell.memory)

    test.log.debug("Get left memory:%s for %s hugepage size "
                   "on host numa node:%s", int(each_node_total_mem - used_mem),
                   hp_size, host_numa_node)

    return each_node_total_mem - used_mem


def get_expected_freepages(vm, params, test, hugepage_size):
    """
    Get the expected freepages for two numa nodes.

    :param vm: vm object
    :param params: dict wrapped with params
    :param test: test object
    :param hugepage_size: huge page size
    """
    all_nodes = params.get("all_nodes")
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm.name)
    numa_tuning = params.get("numa_tuning")
    numa_memnode = vmxml.numa_memnode[0]['mode'] if numa_tuning != "undefined" else ''

    fp0 = int(_get_left_mem(
        vm, params, test, all_nodes[0], hugepage_size) / hugepage_size)
    fp1 = int(_get_left_mem(
        vm, params, test, all_nodes[1], hugepage_size) / hugepage_size)

    fp_list = [fp0, fp1]
    for index, fp in enumerate(fp_list):
        if numa_memnode == "strict" and fp < 0:
            # Check if used virtio memory over total numa node memory, and with
            # strict numa mem mode, it will not use another numa node memory and
            # get 0 free pages.
            fp_list[index] = 0
    return sum(fp_list)


def check_freepages(params, vm, test):
    """
    Check host free pages.

    :param params: dict wrapped with params
    :param vm: vm object
    :param test: test object
    """
    all_nodes = params.get('all_nodes')
    another_hp_size = params.get("another_hp_size")
    no_numa_tuning = params.get("numa_tuning") == "undefined"
    bigger_requested = params.get("case") == "requested_bigger_than_host_numa"

    for node, hp in zip(all_nodes+all_nodes, params.get("hp_list")):
        if hp == another_hp_size and (no_numa_tuning or bigger_requested):
            expected_freepages = get_expected_freepages(vm, params, test, hp)
            actual_freepages = _get_freepages(all_nodes[0], hp) + _get_freepages(all_nodes[1], hp)
            check_item = f"node{all_nodes} hugepage size:{hp} free pages number"
        else:
            expected_freepages = int(_get_left_mem(vm, params, test, node, hp) / hp)
            actual_freepages = _get_freepages(node, hp)
            check_item = f"node{node} hugepage size:{hp} free pages number"

        memory_base.compare_values(
            test,
            expected_freepages,
            actual_freepages,
            check_item=check_item
        )


def run(test, params, env):
    """
    1.Define guest with virtio-mem devices.
    2.Attach virtio-mem and check memory usage by virsh freepages.
    """
    def setup_test():
        """
        Check available numa nodes num.
        """
        test.log.info("TEST_SETUP: Check available numa nodes num")
        params.get("numa_obj").check_numa_nodes_availability()

        test.log.info("TEST_SETUP: Allocate hugepage memory")
        allocate_huge_memory(params, test)

    def run_test():
        """
        Define guest, check xml, check memory
        """
        test.log.info("TEST_STEP: Define guest with numa")
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        define_attrs = create_vm_attrs(params)
        vmxml.setup_attrs(**define_attrs)
        vmxml.sync()

        test.log.info("TEST_STEP: Plug virtio memory devices.")
        mem_objs = create_mem_objects(params)
        plug_virtio_mem(params, vm, operation, mem_objs)

        test.log.info("TEST_STEP:Login the guest and consume guest memory")
        consume_guest_mem(vm, test)

        test.log.info("TEST_STEP: Check the memory usage by virsh freepages")
        check_freepages(params, vm, test)

        if case == "no_source_virtio_mem" and numa_tuning != "undefined":
            test.log.info("TEST_STEP: Check host numa node memory")
            check_numa_memory_allocation(params, test, target_size_1)

        test.log.info("TEST_STEP: Destroy the guest")
        virsh.destroy(vm_name, ignore_status=False)

        test.log.info("TEST_STEP: Check freepages after destroying the guest")
        all_nodes = params.get("all_nodes")
        for node, hp in zip(all_nodes + all_nodes, params.get("hp_list")):
            memory_base.compare_values(
                test,
                expected=params.get("allocate_mem_and_hp_match")[hp]/hp,
                actual=_get_freepages(node, hp),
                check_item=f"node{node} hugepage size:{hp} free pages number"
            )

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        bkxml.sync()
        for file in params.get("cleanup_file"):
            process.run("echo 0 > %s" % file)
        hg_path = params.get("hg_path")
        if hg_path:
            process.run("umount %s; rm %s" % (hg_path, hg_path), ignore_status=True)

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    memory_base.check_supported_version(params, test, vm)

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    case = params.get("case")
    numa_tuning = params.get("numa_tuning")
    operation = params.get("operation")
    target_size_1 = params.get("target_size_1")

    params.update({"numa_obj": numa_base.NumaTest(vm, params, test)})
    default_hp_size, another_hp_size = get_two_hugepage_sizes(test, params)
    params.update({"default_hp_size": default_hp_size})
    params.update({"another_hp_size": another_hp_size})
    params.update({"hp_list":  [default_hp_size, default_hp_size,
                                another_hp_size, another_hp_size]})

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
