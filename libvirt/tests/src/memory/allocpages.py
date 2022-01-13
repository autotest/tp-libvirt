import logging as log

from virttest import virsh
from virttest.staging.utils_memory import get_huge_page_size
from virttest.staging.utils_memory import get_num_huge_pages
from virttest.staging.utils_memory import set_num_huge_pages


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test huge page allocation
    Available huge page size is arch dependent
    """
    try:
        expected_size = params.get("expected_size", "")
        number_of_huge_pages = params.get("number_of_hugepages", "")
        previous_allocation = ""
        previous_allocation = get_num_huge_pages()

        logging.debug("Try to set '%s' huge pages."
                      " Expected size: '%s'. Actual size: '%s'." %
                      (number_of_huge_pages, expected_size,
                       get_huge_page_size()))
        virsh.allocpages(expected_size, number_of_huge_pages,
                         ignore_status=False)

        actual_allocation = get_num_huge_pages()
        if str(actual_allocation) != str(number_of_huge_pages):
            test.fail("Huge page allocation failed."
                      " Expected '%s', got '%s'." %
                      (number_of_huge_pages, actual_allocation))
    finally:
        if previous_allocation:
            logging.debug("Restore huge page allocation")
            set_num_huge_pages(previous_allocation)
