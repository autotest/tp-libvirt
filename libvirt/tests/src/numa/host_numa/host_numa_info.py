import platform
import re

from avocado.utils import memory as mem_utils

from virttest import test_setup
from virttest import virsh
from virttest.libvirt_xml import capability_xml
from virttest.staging import utils_memory
from virttest.utils_misc import NumaNode

from provider.numa import numa_base


def setup_default(test_obj):
    """
    Default setup function for the test

    :param test_obj: NumaTest object
    """
    test_obj.setup()
    test_obj.test.log.debug("Step: setup is done")


def allocate_memory_on_host_nodes(test_obj):
    """
    Allocate hugepage on all host nodes with memory

    :param test_obj: NumaTest object
    :return: dict, allocated info,
                   like {'2048': '100', '1048576': '1'}
    """
    def _allocate_test(page_num, node_id, page_size):
        hpc.set_node_num_huge_pages(page_num, node_id, page_size, ignore_error=True)
        allocated_num = hpc.get_node_num_huge_pages(node_id, page_size)
        if allocated_num < 1:
            test_obj.test.cancel("Can not set at least one page "
                                 "with pagesize '%s' on "
                                 "node '%s'" % (page_size,
                                                node_id))
        return allocated_num

    ret = {}
    all_nodes = test_obj.all_usable_numa_nodes
    allocate_dict = eval(test_obj.params.get('allocate_dict'))
    hpc = test_setup.HugePageConfig(test_obj.params)
    pagesize_list = list(allocate_dict.keys())
    pagesize_list.sort(reverse=True)
    for one_node in all_nodes:
        allocated_info = {}
        for pagesize in pagesize_list:
            test_obj.test.log.debug("To allocate %s %s pages on node %s", allocate_dict[pagesize], pagesize, one_node)
            allocated_num = _allocate_test(allocate_dict[pagesize], one_node, pagesize)
            allocated_info.update({pagesize: allocated_num})
        ret.update({one_node: allocated_info})
        test_obj.test.log.debug("Get allocated information for node %s: '%s'", one_node, allocated_info)
    test_obj.test.log.debug("Get allocated information '%s'", ret)
    test_obj.params['allocate_result'] = ret
    return ret


def verify_node_mem_by_capabilities(test_obj):
    """
    Verify node memory by virsh capabilities

    :param test_obj: NumaTest object
    :param capa_xml: CapabilityXML object
    """
    def _check_cpu_pages():
        """
        Check pages information under <cpu>
        """
        pages_list = capa_xml.cpu_pages
        not_match = [one_page['size'] for one_page in pages_list
                     if int(one_page['size']) not in supported_page_size_list]
        if not_match:
            test_obj.test.fail("The page size %s shown in virsh capabilities "
                               "do not exist in system "
                               "support list '%s'" % (not_match,
                                                      supported_page_size_list))

    def _check_topo_cell():
        """
        Check memory, pages size and pages num under <topology><cells><cell>
        """
        topo_xml = capa_xml.cells_topology
        cells = topo_xml.get_cell(withmem=True)
        for one_cell in cells:
            total_mem = utils_memory.memtotal(node_id=one_cell.cell_id)
            if one_cell.memory != total_mem:
                test_obj.test.fail("Expect node memory '%s', "
                                   "but found '%s'" % (total_mem,
                                                       one_cell.memory))
            pages_list = one_cell.pages
            total_hugepage_mem = 0
            actual_default_page_num = None
            for one_page in pages_list:
                page_size = int(one_page.fetch_attrs()['size'])
                page_num = int(one_page.fetch_attrs()['pages'])
                test_obj.test.log.debug("allocate_result=%s", allocate_result)
                test_obj.test.log.debug("one_page.fetch_attrs()=%s", one_page.fetch_attrs())
                if page_size != default_page_size:
                    pg_num = allocate_result[int(one_cell.cell_id)].get(page_size)
                    if pg_num is not None and pg_num != page_num:
                        test_obj.test.fail("Expect %s pages "
                                           "with page size '%s', but "
                                           "found %s" % (pg_num,
                                                         page_size,
                                                         page_num))
                    total_hugepage_mem += int(page_size) * page_num
                else:
                    actual_default_page_num = page_num
            expect_default_page_num = (total_mem - total_hugepage_mem)//default_page_size
            if expect_default_page_num != int(actual_default_page_num):
                test_obj.test.fail("Expect %s pages with default "
                                   "pagesize '%s', but "
                                   "found '%s'" % (expect_default_page_num,
                                                   default_page_size,
                                                   actual_default_page_num))

    supported_page_size_list = test_obj.params['supported_page_size_list']
    default_page_size = test_obj.params['default_page_size']
    allocate_result = test_obj.params['allocate_result']
    capa_xml = capability_xml.CapabilityXML()
    _check_cpu_pages()
    _check_topo_cell()
    test_obj.test.log.debug("Verify memory value of each node "
                            "by virsh capabilities - PASS")


def common_test_freepages(test_obj, node_id, page_size, page_num, size_unit=''):
    """
    Common function to check freepages

    :param test_obj: NumaTest object
    :param node_id: str, host numa node id
    :param page_size: str, hugepage size in KiB
    :param page_num: str, page number
    :param size_unit: str, page size unit, like KiB, M, G
    """
    ret = virsh.freepages(cellno=node_id,
                          pagesize=page_size,
                          sizeunit=size_unit,
                          **test_obj.virsh_dargs).stdout_text.strip()
    if size_unit == 'M':
        page_size = page_size * 1024
    elif size_unit == 'G':
        page_size = page_size * 1024 * 1024
    search_pattern = "%dKiB:\s+%s" % (page_size, page_num)
    match = re.findall(search_pattern, ret)
    if not match:
        test_obj.test.error("Can not find '%s' in %s" % (search_pattern, ret))
    else:
        test_obj.test.log.debug("Got match '%s' "
                                "for %s", match[0], search_pattern)


def verify_node_mem_by_freepages_all(test_obj):
    """
    Verify node memory by virsh freepages --all

    :param test_obj: NumaTest object
    """
    allocate_result = test_obj.params['allocate_result']
    output = virsh.freepages(options='--all', **test_obj.virsh_dargs).stdout_text.strip()
    default_page_size = test_obj.params['default_page_size']
    all_freepages = {}
    cell_freepages = {}
    output += "\n\n"
    node = None
    for line in output.splitlines():
        if line:
            key, value = line.split(":")
            key = re.findall(r"(\d+)", key)[0]
            if "Node" in line:
                node = int(key)
            else:
                #  Ignore to compare the default page size information
                #  because it changes all the time
                if int(key) == default_page_size:
                    continue
                else:
                    cell_freepages[int(key)] = int(value.strip())
        else:
            all_freepages.update({node: cell_freepages})
            cell_freepages = {}

    test_obj.test.log.debug("Parsed all freepages "
                            "in 'virsh freepages --all': %s", all_freepages)
    for node in all_freepages.keys():
        page_info = all_freepages[node]
        for pgsize, pgnum in page_info.items():
            test_obj.test.log.debug("node %d, pgsize %d, pgnum %d", node, pgsize, pgnum)
            if allocate_result[node].get(pgsize) is not None and pgnum != allocate_result[node][pgsize]:
                test_obj.test.fail("Expect freepages "
                                   "result has '%d' pages "
                                   "for size '%d' on node %d, but "
                                   "found '%d'" % (allocate_result[node][pgsize],
                                                   pgsize, node, pgnum))
            elif allocate_result[node].get(pgsize) is None and pgnum != 0:
                test_obj.test.fail("Expect freepages "
                                   "result has 0 page "
                                   "for size '%d' on node %d, but "
                                   "found '%d'" % (pgsize, node, pgnum))
    test_obj.test.log.debug("Verify node memory by freepages with --all - PASS")


def verify_node_mem_by_freepages(test_obj):
    """
    Verify node memory by virsh freepages without options

    :param test_obj: NumaTest object
    """
    allocate_result = test_obj.params['allocate_result']
    all_nodes = test_obj.all_usable_numa_nodes
    for one_node in all_nodes:
        for pgsize in allocate_result[one_node].keys():
            common_test_freepages(test_obj,
                                  one_node,
                                  pgsize,
                                  allocate_result[one_node][pgsize])
    test_obj.test.log.debug("Verify huge page memory info on "
                            "each host numa node - PASS")


def verify_node_mem_by_freepages_various_unit(test_obj):
    """
    Verify node memory by virsh freepages with different size units

    :param test_obj: NumaTest object
    """
    allocate_result = test_obj.params['allocate_result']
    all_nodes = test_obj.all_usable_numa_nodes
    for pgsize in allocate_result[all_nodes[1]].keys():
        common_test_freepages(test_obj,
                              all_nodes[1],
                              pgsize, allocate_result[all_nodes[1]][pgsize])
        common_test_freepages(test_obj,
                              all_nodes[1],
                              pgsize,
                              allocate_result[all_nodes[1]][pgsize],
                              size_unit='KiB')
        if int(pgsize) >= 1024:
            common_test_freepages(test_obj,
                                  all_nodes[1],
                                  int(pgsize)//1024,
                                  allocate_result[all_nodes[1]][pgsize],
                                  size_unit='M')
        if int(pgsize) >= (1024*1024):
            common_test_freepages(test_obj,
                                  all_nodes[1],
                                  int(pgsize)//(1024*1024),
                                  allocate_result[all_nodes[1]][pgsize],
                                  size_unit='G')
    test_obj.test.log.debug("Verify huge page memory info on "
                            "the first host numa node with different "
                            "page size unit - PASS")


def verify_node_distance_by_capabilities(test_obj):
    """
    Verify node distance by virsh capabilities

    :param test_obj: NumaTest object
    """
    topo_xml = capability_xml.CapabilityXML().cells_topology
    cells = topo_xml.get_cell(withmem=True)
    for one_cell in cells:
        siblings = [sibling['value'] for sibling in one_cell.sibling]
        distances = test_obj.host_numa_info.get_node_distance(one_cell.cell_id)
        if siblings != distances:
            test_obj.test.fail("For node '%s', "
                               "expect the distances to "
                               "be '%s', but "
                               "found '%s'" % (one_cell.cell_id,
                                               distances,
                                               siblings))
    test_obj.test.log.debug("Verify node distance by "
                            "capabilities - PASS")


def verify_node_cpus_by_capabilities(test_obj):
    """
    Verify node cpus by virsh capabilities

    :param test_obj: NumaTest object
    """
    topo_xml = capability_xml.CapabilityXML().cells_topology
    cells = topo_xml.get_cell(withmem=True)
    node_cpus = test_obj.host_numa_info.online_nodes_cpus
    for one_cell in cells:
        expect_cpu_num = len(node_cpus[int(one_cell.cell_id)].strip().split())
        if int(one_cell.cpus_num) != expect_cpu_num:
            test_obj.test.fail("For node '%s', expect "
                               "cpus number to be '%s', "
                               "but found '%s'" % (one_cell.cell_id,
                                                   expect_cpu_num,
                                                   one_cell.cpus_num))
        if len(one_cell.cpu) != expect_cpu_num:
            test_obj.test.fail("for node '%s', expect "
                               "cpu flag number to be '%s', "
                               "but found '%s'" % (one_cell.cell_id,
                                                   expect_cpu_num,
                                                   len(one_cell.cpu)))
        verify_node_cpus_under_cell(test_obj, one_cell.cpu, one_cell.cell_id)

    test_obj.test.log.debug("Verify node cpus by capabilities - PASS")


def verify_node_cpus_under_cell(test_obj, cpu_list, node_id):
    """
    Verify node cpus under <topology><cells><cell> in virsh capabilities

    :param test_obj: NumaTest object
    :param cpu_list: list, cpu list under<topology><cells><cell><cpus>
    :param node_id: str, host numa node id
    """
    one_numa_node = NumaNode(int(node_id) + 1)
    for one_cpu_dict in cpu_list:
        cpu_info_in_sys = one_numa_node.get_cpu_topology(one_cpu_dict['id'])
        if one_cpu_dict != cpu_info_in_sys:
            test_obj.test.fail("Expect cpu '%s' in node '%s' "
                               "to be '%s', but found "
                               "'%s'" % (one_cpu_dict['id'],
                                         node_id,
                                         cpu_info_in_sys,
                                         one_cpu_dict))
    test_obj.test.log.debug("Verify node cpus under cell - PASS")


def run_default(test_obj):
    """
    Default run function for the test

    :param test_obj: NumaTest object
    """
    supported_hugepage_sizes = mem_utils.get_supported_huge_pages_size()
    default_page_size = mem_utils.get_page_size()//1024
    supported_page_size_list = supported_hugepage_sizes + [default_page_size]
    test_obj.params['default_page_size'] = default_page_size
    test_obj.params['supported_page_size_list'] = supported_page_size_list

    allocate_memory_on_host_nodes(test_obj)
    verify_node_mem_by_capabilities(test_obj)
    verify_node_mem_by_freepages_all(test_obj)
    verify_node_mem_by_freepages(test_obj)
    verify_node_mem_by_freepages_various_unit(test_obj)
    verify_node_distance_by_capabilities(test_obj)
    if 'aarch64' not in platform.machine().lower():
        verify_node_cpus_by_capabilities(test_obj)


def teardown_default(test_obj):
    """
    Default teardown function for the test

    :param test_obj: NumaTest object
    """
    test_obj.teardown()

    allocate_dict = eval(test_obj.params.get('allocate_dict'))
    hpc = test_setup.HugePageConfig(test_obj.params)
    for one_psize in allocate_dict.keys():
        hpc.set_kernel_hugepages(one_psize, 0)

    test_obj.test.log.debug("Step: teardown is done")


def run(test, params, env):
    """
    Test numa topology together with cpu topology
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    numatest_obj = numa_base.NumaTest(vm, params, test)
    try:
        setup_default(numatest_obj)
        run_default(numatest_obj)
    finally:
        teardown_default(numatest_obj)
