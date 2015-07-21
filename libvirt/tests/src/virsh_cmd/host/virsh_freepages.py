import logging
from autotest.client.shared import error
from autotest.client import utils
from virttest.staging import utils_memory
from virttest import virsh
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
    cellno_list = []
    get_node_num = "numactl -H|awk '/available:/ {print $2}'"
    host_cells = utils.run(get_node_num).stdout.strip()
    try:
        host_cells = int(host_cells)
    except (TypeError, ValueError), e:
        raise error.TestError("Fail to get host nodes number: %s" % e)
    if cellno == "AUTO":
        cellno_list = range(0, host_cells)
    elif cellno == "OUT_OF_RANGE":
        cellno_list.append(host_cells)
    else:
        cellno_list.append(cellno)
    pagesize = params.get("freepages_page_size")
    default_pagesize = utils.run("getconf PAGE_SIZE").stdout.strip()
    try:
        default_pagesize = int(default_pagesize) / 1024
    except (TypeError, ValueError), e:
        raise error.TestError("Fail to get default page size: %s" % e)
    default_huge_pagesize = utils_memory.get_huge_page_size()
    pagesize_list = []
    if pagesize == "AUTO":
        pagesize_list.append(default_pagesize)
        pagesize_list.append(default_huge_pagesize)
    else:
        pagesize_list.append(pagesize)
    huge_pages = params.get("huge_pages")
    if huge_pages and not status_error:
        try:
            huge_pages = int(huge_pages)
        except (TypeError, ValueError), e:
            raise error.TestError("Huge page value %s shoule be an integer"
                                  % huge_pages)
        # Set huge page for positive test
        node_list = "0-%s" % (host_cells - 1)
        utils.run("numactl -m %s echo %s >/proc/sys/vm/nr_hugepages_mempolicy"
                  % (node_list, huge_pages * host_cells))

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
            if not status_error and huge_pages and page == default_huge_pagesize:
                page_k = "%sKiB" % page
                if cellno is not None:
                    for i in range(0, host_cells):
                        node_v = "Node %s" % i
                else:
                    node_v = "Node %s" % cell
                    expect_result_list = [{"Node": node_v, page_k: huge_pages}]
                if check_freepages(result.stdout.strip(), expect_result_list):
                    logging.info("Huge page freepages check pass")
                else:
                    raise error.TestFail("Huge page freepages check failed,"
                                         " expect result is %s" %
                                         expect_result_list)
