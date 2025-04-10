import logging as log
import time

from avocado.utils import memory as avocado_mem

from virttest import virsh
from virttest import test_setup
from virttest import utils_misc
from virttest.utils_test import libvirt as utlv


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def check_freepages(output, expect_result_list):
    """
    Check output of virsh freepages
    For 4KiB freepages checking, if the gap between freepages command return
    with system current freememory is less than 4*1024Kib, check would pass.

    :params output: Virsh cmd output, which has 3 types:
        # virsh freepages --all
        Node 0:
        4KiB: 15268666
        2048KiB: 0

        Node 1:
        4KiB: 15833180
        2048KiB: 0

        # virsh freepages --pagesize 2048 --all
        Node 0:
        2048KiB: 0

        Node 1:
        2048KiB: 0

        # virsh freepages --cellno 0 --pagesize 2048
        2048KiB: 0
    :params expect_result_list: A list of expect result:
        [{"Node": "Node 0", "2048KiB": 1}]
    :return: True for check pass
    """
    all_freepages = []
    cell_freepages = {}
    output += "\n\n"
    for line in output.splitlines():
        if line:
            key, value = line.split(":")
            if "Node" in key:
                cell_freepages["Node"] = key
            else:
                cell_freepages[key] = value.strip()
        else:
            all_freepages.append(cell_freepages)
            cell_freepages = {}
    check_result = []
    for expect in expect_result_list:
        check_pass = False
        if "Node" in expect.keys():
            # Find cell with same Node
            for cell in all_freepages:
                logging.info(set(cell.items()))
                logging.info(set(expect.items()))
                if cell['Node'] == expect['Node']:
                    break
            modify_expect(cell, expect)
            if set(cell.items()) == set(expect.items()):
                check_pass = True
                break
        else:
            cell = all_freepages[0]
            modify_expect(cell, expect)
            if expect == cell:
                check_pass = True
        check_result.append(check_pass)
    return False not in check_result


def modify_expect(cell, expect):
    """
    For default page size freepages checking, if the gap between freepages command return
    with system current freememory is less than 'default page size' * 1024Kib, check would pass.

    :params cell: free pages dict of single node
    :params expect: expected free page dict
    """
    default_pagesize = int(avocado_mem.get_page_size() / 1024)
    pagesize_key = f'{default_pagesize}KiB'
    if pagesize_key in expect.keys():
        if abs(int(float(expect[pagesize_key])) - int(float(cell[pagesize_key]))) < 1024:
            expect[pagesize_key] = cell[pagesize_key]


def run(test, params, env):
    """
    Test virsh command virsh freepages
    """

    def get_freepages_number_of_node(node, pagesize):
        """
        Get number of hugepages for node

        :params node: node No
        :params pagesize: page size, for example: '4', '2048', '1048576'
            For pagesize is default page size which is not hugepage, get free memory from
                `grep MemFree /sys/devices/system/node/node${node}/meminfo`
            For pagesize '2048' or '1048576',  get free page number from
                /sys/devices/system/node/nodei${node}/hugepages/
                hugepages-${pagesize}/free_hugepages
        :return: integer number of free pagesize
        """
        if pagesize == default_pagesize:
            node_meminfo = host_numa_node.read_from_node_meminfo(node, 'MemFree')
            return int(node_meminfo) / int(default_pagesize)
        return hp_cl.get_node_num_huge_pages(node, pagesize, type='free')

    def check_hugepages_allocated_status(pagesize, quantity):
        """
        Allocate some specific hugepages and check whether the number of this
        specific hugepages allocated by kernel totally is equal to the sum that
        allocated for each nodes, and also check if the number of freepages
        for each node is equal to the number allocated by kernel for each
        node. If yes, return True, else return False.

        :params pagesize: pagesize type needed to check
        :params quantity: the number will be allocated
        :return: True if current system support this pagesize and allocate
                 page successfully, otherwise return False
        """
        hp_cl.set_kernel_hugepages(pagesize, quantity)
        # kernel need some time to prepare hugepages due to maybe there
        # are some memory fragmentation.
        time.sleep(1)
        # Check hugepages allocated by kernel is equal to the sum of all nodes
        actual_quantity = hp_cl.get_kernel_hugepages(pagesize)
        if 0 == actual_quantity:
            # Not enough free memory for support current hugepages type.
            logging.debug('%s is not available.' % pagesize)
            return False
        else:
            hp_sum = 0
            for node in node_list:
                try:
                    allocate_pages = hp_cl.get_node_num_huge_pages(node, pagesize)
                    free_pages = hp_cl.get_node_num_huge_pages(node, pagesize, type='free')
                    if allocate_pages != free_pages:
                        # Check if allocated hugepages is equal to freepages for
                        # each node
                        logging.warning("allocated %sKiB hugepages is not equal to "
                                        "free pages on node%s." % (pagesize, node))
                        oth_node.append(node)
                        continue
                    hp_sum += allocate_pages
                except ValueError as exc:
                    logging.warning("%s", str(exc))
                    logging.debug('move node %s out of testing node list', node)
                    oth_node.append(node)
            for node in oth_node:
                node_list.remove(node)
            if hp_sum != actual_quantity:
                test.error("the sum of %sKiB hugepages of all nodes is not "
                           "equal to kernel allocated totally." % pagesize)
            return True

    def cleanup():
        """
        Clean up all supported huge page size allocation
        """
        for hp_size in hp_cl.get_multi_supported_hugepage_size():
            hp_cl.set_kernel_hugepages(hp_size, 0)

    option = params.get("freepages_option", "")
    status_error = "yes" == params.get("status_error", "no")
    cellno = params.get("freepages_cellno")
    pagesize = params.get("freepages_pagesize")
    pagesize_kb = params.get("pagesize_kb")
    hugepage_allocation_dict = eval(params.get("hugepage_allocation_dict", "{}"))

    host_numa_node = utils_misc.NumaInfo()
    node_list = host_numa_node.get_online_nodes_withmem()
    oth_node = []

    hp_cl = test_setup.HugePageConfig(params)
    supported_hp_size = hp_cl.get_multi_supported_hugepage_size()

    default_pagesize = str(int(avocado_mem.get_page_size() / 1024))

    cellno_list = []
    if cellno == "EACH":
        cellno_list = node_list
    elif cellno == "OUT_OF_RANGE":
        cellno_list.append(max(node_list) + 1)
    else:
        cellno_list.append(cellno)

    # Add default pagesize
    supported_hp_size.insert(0, str(int(avocado_mem.get_page_size() / 1024)))
    pagesize_list, pagesize_kb_list = [], []
    if pagesize == "EACH":
        pagesize_list = pagesize_kb_list = supported_hp_size
    else:
        pagesize_list.append(pagesize)
        pagesize_kb_list.append(pagesize_kb)

    if not status_error:
        for page_size in pagesize_kb_list:
            if page_size is None or page_size == default_pagesize:
                continue
            if page_size not in supported_hp_size:
                test.cancel("Required hugepage size %sKiB is not supported on this host." % page_size)
            if not check_hugepages_allocated_status(page_size, hugepage_allocation_dict[page_size]):
                test.cancel("Not enough %sKiB hugepage size allocated in current system." % page_size)

    # Run test
    for cell in cellno_list:
        for page in pagesize_list:
            result = virsh.freepages(cellno=cell,
                                     pagesize=page,
                                     options=option,
                                     debug=True)
            # Unify pagesize unit to KiB
            if page is not None and pagesize_kb is not None:
                page = pagesize_kb
            # the node without memory will fail and it is expected
            if cell is not None and page is None and not status_error:
                status_error = True

            skip_errors = [r'page size.*is not available on node']
            utlv.check_result(result, skip_if=skip_errors, any_error=status_error)

            expect_result_list = []
            # Check huge pages
            if not status_error:
                if '--all' == option:
                    # For --all
                    for i in node_list:
                        node_dict = {}
                        node_dict['Node'] = "Node %s" % i
                        if page is None:
                            # For --all(not specify specific pagesize)
                            for page_t in supported_hp_size:
                                page_k = "%sKiB" % page_t
                                node_dict[page_k] = str(get_freepages_number_of_node(i, page_t))
                        else:
                            # For --all --pagesize
                            page_k = "%sKiB" % page
                            node_dict[page_k] = str(get_freepages_number_of_node(i, page))
                        expect_result_list.append(node_dict)
                else:
                    # For --cellno
                    if page is not None:
                        # For --cellno --pagesize
                        node_dict = {}
                        page_k = "%sKiB" % page
                        node_dict[page_k] = str(get_freepages_number_of_node(cell, page))
                        expect_result_list = [node_dict]

                if check_freepages(result.stdout.strip(), expect_result_list):
                    logging.info("Huge page freepages check pass")
                else:
                    test.fail("Huge page freepages check failed, "
                              "expect result is %s" % expect_result_list)

    # clean up the huge page allocation
    cleanup()
