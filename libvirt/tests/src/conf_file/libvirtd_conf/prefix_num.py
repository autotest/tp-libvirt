import logging as log
import ast
import os


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def get_prefix(check_list):
    """
    Get the prefix of configure file
    :param check_list: A list of configure file
    :return: A dict of configure file's prefix and libvirtd's prefix
    """
    all_conf_list = os.listdir("/usr/lib/sysctl.d/")
    logging.debug("All conf list: %s" % all_conf_list)

    prefix_list = {}
    libvirtd_num = None
    # Get the prefix of conf file
    for conf in all_conf_list:
        for value in check_list:
            if value in conf:
                prefix_list[value] = conf.split("-")[0]
        if "libvirtd.conf" in conf:
            libvirtd_num = conf.split("-")[0]
            logging.debug("The prefix of libvirtd.conf: %s" % libvirtd_num)
    logging.debug("The prefix list: %s" % prefix_list)
    return prefix_list, libvirtd_num


def run(test, params, env):
    """
    Check the prefix of libvird.conf.

    1) Get the prefix of all conf file
    2) Check the prefix of libvirtd.conf.
    """
    check_list = ast.literal_eval(params.get("check_list", "[]"))
    logging.debug("Check list: %s" % check_list)

    prefix_check_list = {}
    libvirtd_prefix_num = None
    prefix_check_list, libvirtd_prefix_num = get_prefix(check_list)
    # Check libvirtd.conf
    if libvirtd_prefix_num:
        # Check prefix
        for key, value in prefix_check_list.items():
            if int(value) > int(libvirtd_prefix_num):
                test.fail("The prefix of libvirtd.conf less than %s." % key)
    else:
        test.fail("Not find the prefix of libvirtd.conf.")
