import logging

from autotest.client.shared import error

from virttest import virsh
from virttest import test_setup
from virttest import utils_misc
from virttest.utils_test import libvirt as utlv


def check_freepages(output, expect_result_list):
    """
    Check output of virsh freepages, as the freepages of default pagesize
    changing all the time, so only check huge page here.
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
            for cell in all_freepages:
                logging.info(set(cell.items()))
                logging.info(set(expect.items()))
                if set(cell.items()) >= set(expect.items()):
                    check_pass = True
                    break
        else:
            if expect == all_freepages[0]:
                check_pass = True
        check_result.append(check_pass)
    return False not in check_result


def run(test, params, env):
    """
    Test virsh command virsh freepages
    """

    option = params.get("freepages_option", "")
    status_error = "yes" == params.get("status_error", "no")
    cellno = params.get("freepages_cellno")
    pagesize = params.get("freepages_page_size")
    huge_pages_num = params.get("huge_pages_num")

    hp_cl = test_setup.HugePageConfig(params)
    default_hp_size = hp_cl.get_hugepage_size()
    supported_hp_size = hp_cl.get_multi_supported_hugepage_size()

    host_numa_node = utils_misc.NumaInfo()
    node_list = host_numa_node.online_nodes
    logging.debug("host node list is %s", node_list)

    for i in node_list:
        try:
            hp_cl.get_node_num_huge_pages(i, default_hp_size)
        except ValueError, e:
            logging.warning("%s", e)
            logging.debug('move node %s out of testing node list', i)
            node_list.remove(i)

    cellno_list = []
    if cellno == "AUTO":
        cellno_list = node_list
    elif cellno == "OUT_OF_RANGE":
        cellno_list.append(max(node_list) + 1)
    else:
        cellno_list.append(cellno)

    pagesize_list = []
    if pagesize == "AUTO":
        pagesize_list = supported_hp_size
    else:
        pagesize_list.append(pagesize)

    if huge_pages_num and not status_error:
        try:
            huge_pages_num = int(huge_pages_num)
        except (TypeError, ValueError), e:
            raise error.TestError("Huge page value %s shoule be an integer" %
                                  huge_pages_num)
        # Set huge page for positive test
        for i in node_list:
            hp_cl.set_node_num_huge_pages(huge_pages_num, i, default_hp_size)

    # Run test
    for cell in cellno_list:
        for page in pagesize_list:
            result = virsh.freepages(cellno=cell,
                                     pagesize=page,
                                     options=option,
                                     debug=True)
            utlv.check_exit_status(result, status_error)
            expect_result_list = []
            # Check huge pages
            if (not status_error and
                    huge_pages_num and
                    page == str(default_hp_size)):
                page_k = "%sKiB" % page
                node_dict = {page_k: str(huge_pages_num)}
                if cell is None:
                    for i in node_list:
                        tmp_dict = {}
                        tmp_dict['Node'] = "Node %s" % i
                        tmp_dict.update(node_dict)
                        expect_result_list.append(tmp_dict)
                else:
                    expect_result_list = [node_dict]

                if check_freepages(result.stdout.strip(), expect_result_list):
                    logging.info("Huge page freepages check pass")
                else:
                    raise error.TestFail("Huge page freepages check failed,"
                                         " expect result is %s" %
                                         expect_result_list)
