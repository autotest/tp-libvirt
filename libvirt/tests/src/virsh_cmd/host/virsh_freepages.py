import logging
import time

from virttest import virsh
from virttest import test_setup
from virttest import utils_misc
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt as utlv


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


def modify_expect(cell, expect, pagesize='4KiB'):
    """
    For 4KiB freepages checking, if the gap between freepages command return
    with system current freememory is less than 4*1024Kib, check would pass.
    """
    if pagesize in expect.keys():
        if abs(int(float(expect[pagesize])) - int(float(cell[pagesize]))) < 1024:
            expect[pagesize] = cell[pagesize]


def run(test, params, env):
    """
    Test virsh command virsh freepages
    """

    def get_freepages_number_of_node(node, pagesize):
        """
        Get number of hugepages for node

        :params node: node No
        :params pagesize: page size, for example: '4', '2048', '1048576'
            For pagesize is '4' which is not hugepage, get free memory from
                `grep MemFree /sys/devices/system/node/node${node}/meminfo`
            For pagesize '2048' or '1048576',  get free page number from
                /sys/devices/system/node/nodei${node}/hugepages/
                hugepages-${pagesize}/free_hugepages
        :return: integer number of free pagesize
        """
        if pagesize == '4':
            node_meminfo = host_numa_node.read_from_node_meminfo(node, 'MemFree')
            return int(node_meminfo) / 4
        else:
            return hp_cl.get_node_num_huge_pages(node, pagesize, type='free')

    def check_hugepages_allocated_status(is_type_check, pagesize, quantity):
        """
        Allocate some specific hugepages and check whether the number of this
        specific hugepages allocated by kernel totally is equal to the sum that
        allocated for each nodes, and also check if the number of freepages
        for each node is equal to the number allocated by kernel for each
        node. If yes, return True, else return False.

        :params is_type_check: Flag to determine do this check or not
        :params pagesize: pagesize type needed to check
        :params quantity: the number will be allocated
        :return: True if current system support this pagesize and allocate
                 page successfully, otherwise return False
        """
        if not is_type_check or pagesize not in supported_hp_size:
            return False
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

    option = params.get("freepages_option", "")
    status_error = "yes" == params.get("status_error", "no")
    cellno = params.get("freepages_cellno")
    pagesize = params.get("freepages_pagesize")

    host_numa_node = utils_misc.NumaInfo()
    node_list = host_numa_node.get_online_nodes_withmem()
    oth_node = []

    hp_cl = test_setup.HugePageConfig(params)
    supported_hp_size = hp_cl.get_multi_supported_hugepage_size()

    cellno_list = []
    if cellno == "EACH":
        cellno_list = node_list
    elif cellno == "OUT_OF_RANGE":
        cellno_list.append(max(node_list) + 1)
    else:
        cellno_list.append(cellno)

    # Add 4K pagesize
    supported_hp_size.insert(0, '4')
    pagesize_list = []
    if pagesize == "EACH":
        pagesize_list = supported_hp_size
    else:
        pagesize_list.append(pagesize)

    if not status_error:
        # Get if CPU support 2M-hugepages
        check_2M = vm_xml.VMCPUXML.check_feature_name('pse')
        # Get if CPU support 1G-hugepages
        check_1G = vm_xml.VMCPUXML.check_feature_name('pdpe1gb')
        if not check_2M and not check_1G:
            test.cancel("pse and pdpe1gb flags are not supported by CPU.")

        num_1G = str(1024*1024)
        num_2M = str(2048)

        # Let kernel allocate 64 of 2M hugepages at runtime
        quantity_2M = 64
        # Let kernel allocate 2 of 1G hugepages at runtime
        quantity_1G = 2
        check_2M = check_hugepages_allocated_status(check_2M, num_2M, quantity_2M)
        check_1G = check_hugepages_allocated_status(check_1G, num_1G, quantity_1G)

        if not check_2M and not check_1G:
            test.cancel("Not enough free memory to support huge pages "
                        "in current system.")

    # Run test
    for cell in cellno_list:
        for page in pagesize_list:
            result = virsh.freepages(cellno=cell,
                                     pagesize=page,
                                     options=option,
                                     debug=True)
            # Unify pagesize unit to KiB
            if page is not None:
                if page.upper() in ['2M', '2048K', '2048KIB']:
                    page = num_2M
                elif page.upper() in ['1G', '1048576K', '1048576KIB']:
                    page = num_1G
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
