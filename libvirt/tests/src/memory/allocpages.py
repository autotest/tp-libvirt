from avocado.utils import process

from virttest import virsh
from virttest.libvirt_xml import capability_xml
from virttest.staging.utils_memory import get_huge_page_size
from virttest.staging.utils_memory import get_num_huge_pages
from virttest.staging.utils_memory import set_num_huge_pages
from virttest.utils_test import libvirt


def test_s390x_1M(params, test):
    """
    Test for 1M hugepage on s390x

    :param params: dict, test parameters
    :param test: test object
    """
    page_size = params.get("page_size", "")
    page_count = params.get("page_count", "")

    previous_allocation = get_num_huge_pages()
    params['previous_allocation'] = previous_allocation
    test.log.debug("Try to set '%s' huge pages. "
                   "Expected size: '%s'. Actual "
                   "size: '%s'." % (page_count,
                                    page_size,
                                    get_huge_page_size()))
    virsh.allocpages(page_size, page_count,
                     ignore_status=False)

    actual_allocation = get_num_huge_pages()
    if str(actual_allocation) != str(page_count):
        test.fail("Huge page allocation failed. "
                  "Expected '%s', got '%s'." % (page_count,
                                                actual_allocation))


def teardown_s390x_1M(params, test):
    """
    Teardown for 1M hugepage test on s390x

    :param params: dict, test parameters
    :param test: test object
    """
    previous_allocation = int(params.get('previous_allocation', '0'))
    test.log.debug("Restore huge page allocation")
    set_num_huge_pages(previous_allocation)


def get_cell_ids(test):
    """
    Get cell ids on the host

    :param test: test object
    :return: list, cell ids, like ['0', '1']
    """
    capxml = capability_xml.CapabilityXML()
    cell_topology = capxml.get_cells_topology()
    cell_list = cell_topology.get_cell()
    cell_ids = [cell_list[index].cell_id for index in range(0, len(cell_list))]
    test.log.debug("Cell IDs on the host: {}".format(cell_ids))
    return cell_ids


def backup_old_page_nums(params, cell_ids):
    """
    Backup page numbers for all cells on the host

    :param params: dict, test parameters
    :param cell_ids: list, cell id on the host, like ['0', '1']
    """
    old_cell_id_page_num = {}
    for node in cell_ids:
        cmd_check_freepage = params.get("cmd_check_freepage")
        cmd_check_freepage = cmd_check_freepage.format(node)
        free_page_num = process.run(cmd_check_freepage,
                                    shell=True,
                                    verbose=True).stdout_text.strip()
        old_cell_id_page_num.update({node: free_page_num})
    params['old_cell_id_page_num'] = old_cell_id_page_num


def test_with_options(params, test):
    """
    Test with multiple options of virsh allocpages

    :param params: dict, test parameters
    :param test: test object
    """
    page_size = params.get("page_size")
    page_count = params.get("page_count")
    virsh_option = {'debug': True, 'ignore_status': False}
    current_cell_id_page_num = {}

    test.log.debug("Step: Get the cell ids from capabilities")
    cell_ids = get_cell_ids(test)

    test.log.debug("Step: Backup old free page numbers for "
                   "page size {}".format(page_size))
    backup_old_page_nums(params, cell_ids)

    test.log.debug("Step: Set page number without any options")
    virsh.allocpages(page_size, page_count, **virsh_option)
    verify_total_free_page_number(current_cell_id_page_num, cell_ids, params, test)

    test.log.debug("Step: Add page number to a specified cell with --add --cellno")
    virsh.allocpages(page_size, page_count,
                     extra='--add --cellno %s' % cell_ids[0],
                     **virsh_option)
    verify_free_page_number(current_cell_id_page_num, [cell_ids[0]],
                            params, '--add --cellno %s' % cell_ids[0], test)

    test.log.debug("Step: Set page number to all cells with --all")
    virsh.allocpages(page_size, int(page_count) * 2,
                     extra='--all', **virsh_option)
    verify_free_page_number(current_cell_id_page_num, cell_ids,
                            params, "--all", test)

    test.log.debug("Step: Add page number to all cells with --add --all")
    virsh.allocpages(page_size, page_count,
                     extra='--add --all', **virsh_option)
    verify_free_page_number(current_cell_id_page_num, cell_ids,
                            params, "--add --all", test)


def verify_free_page_number(current_cell_id_page_num, cell_ids, params, extra_option, test):
    """
    Verify free page number for specified cells

    :param current_cell_id_page_num: dict, current cell id and
            its free page number, like {'0': '3', '1': '5'}
    :param cell_ids: list, cell id on the host, like ['0', '1']
    :param params: dict, test parameters
    :param extra_option: str, virsh extra options
    :param test: test object
    """
    def _simple_verify_number(cell_id, cmd_check, num_expected):
        """
        Utility to verify page number

        :param cell_id: str, cell id
        :param cmd_check: str, command to use for checking
        :param num_expected: expected page number
        """
        ret = process.run(cmd_check,
                          shell=True,
                          verbose=True).stdout_text.strip()
        current_cell_id_page_num.update({node: ret})
        if int(ret) != num_expected:
            test.fail("Expect page number on cell {} to be "
                      "{}, but found {}".format(cell_id,
                                                num_expected,
                                                ret))
        else:
            test.log.debug("Page number on cell {} is {} as "
                           "expected".format(cell_id, ret))

    page_count = params.get("page_count")
    for node in cell_ids:
        cmd_check_freepage = params.get("cmd_check_freepage")
        cmd_check_freepage = cmd_check_freepage.format(node)
        if extra_option.count('--add'):
            free_page_num_expected = int(current_cell_id_page_num[node]) + int(page_count)
        else:
            free_page_num_expected = int(page_count) * 2
        _simple_verify_number(node, cmd_check_freepage, free_page_num_expected)


def verify_total_free_page_number(current_cell_id_page_num, cell_ids, params, test):
    """
    Verify total free page number for all cells

    :param current_cell_id_page_num: dict, current cell id and
            its free page number, like {'0': '3', '1': '5'}
    :param cell_ids: list, cell id on the host, like ['0', '1']
    :param params: dict, test parameters
    :param test: test object
    """
    page_count = int(params.get("page_count"))
    page_size = int(params.get("page_size"))
    total_page_number_expected = page_count
    total_page_number = 0

    for node in cell_ids:
        cmd_check_freepage = params.get("cmd_check_freepage")
        cmd_check_freepage = cmd_check_freepage.format(node)
        free_page_num = process.run(cmd_check_freepage,
                                    shell=True,
                                    verbose=True).stdout_text.strip()
        current_cell_id_page_num.update({node: free_page_num})
        total_page_number += int(free_page_num)
    if total_page_number != total_page_number_expected:
        test.fail("Expect total page number for page size {} "
                  "to be {}, but found {}".format(page_size,
                                                  total_page_number_expected,
                                                  total_page_number))
    else:
        test.log.debug("Total page number on all nodes {} is "
                       "{} as expected".format(cell_ids,
                                               total_page_number))


def teardown_with_options(params, test):
    """
    Teardown for the test with options

    :param params: dict, test parameters
    :param test: test object
    """
    old_cell_id_page_num = params.get('old_cell_id_page_num')
    page_size = params.get("page_size")
    virsh_option = {'debug': True, 'ignore_status': False}

    if old_cell_id_page_num:
        test.log.debug("Teardown: Recover the page numbers")
        for node, page_num in old_cell_id_page_num.items():
            virsh.allocpages(page_size, page_num,
                             extra='--cellno %s' % node,
                             **virsh_option)


def test_readonly(params, test):
    """
    Test virsh allocpages in readonly mode

    :param params: dict, test parameters
    :param test: test object
    """
    page_size = params.get("page_size")
    page_count = params.get("page_count")
    readonly = params.get("readonly")
    error_msg = params.get("err_msg")
    virsh_option = {'debug': True, 'ignore_status': True, 'readonly': readonly}
    test.log.debug("Step: Set page number with readonly")
    ret = virsh.allocpages(page_size, page_count, **virsh_option)
    libvirt.check_result(ret, expected_fails=error_msg)


def teardown_default(params, test):
    """
    Default teardown method

    :param params: dict, test parameters
    :param test: test object
    """
    pass


def run(test, params, env):
    """
    Test huge page allocation
    Available huge page size is arch dependent
    """
    test_case = params.get('test_case', "")
    run_test = eval("test_%s" % test_case)
    teardown_test = eval("teardown_%s" % test_case) \
        if "teardown_%s" % test_case in globals() else teardown_default
    try:
        run_test(params, test)
    finally:
        teardown_test(params, test)
