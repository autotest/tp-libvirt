#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Red Hat
#
#   SPDX-License-Identifier: GPL-2.0
#
#   Author: Dan Zheng <dzheng@redhat.com>
#

import os
import re

from virttest import virsh


def set_ksm_parameters_by_virsh(params, test):
    """
    Set ksm parameter via virsh node-memory-tune

    :param params: dict, test parameters
    :param test: test object
    """
    pages_to_scan = params.get('shm_pages_to_scan')
    sleep_millisecs = params.get('shm_sleep_millisecs')
    merge_across_nodes = params.get('shm_merge_across_nodes')
    virsh.node_memtune(shm_pages_to_scan=pages_to_scan,
                       shm_sleep_millisecs=sleep_millisecs,
                       shm_merge_across_nodes=merge_across_nodes,
                       debug=True, ignore_status=False)
    test.log.debug("Set ksm parameters successfully")


def get_ksm_parameters_by_virsh(params, test):
    """
    Get ksm parameter values via virsh node-memory-tune

    :param params: dict, test parameters
    :param test: test object
    :return: dict, ksm parameter values
    """
    result = virsh.node_memtune(debug=True, ignore_status=False)
    set_ksm_values = eval(params.get("set_ksm_values"))
    ksm_params = {}
    pattern = "\s+(\S+)\s+(\d+)"
    matches = re.findall(pattern, result.stdout_text.strip())
    for ksm_key in set_ksm_values.keys():
        for item in matches:
            if ksm_key == item[0]:
                ksm_params.update({ksm_key: item[1]})
                break
    test.log.debug("Return ksm parameters:%s", ksm_params)
    return ksm_params


def get_ksm_sysfs_config(params, test):
    """
    Get ksm values in related kernel config files

    :param params: dict, test parameters
    :param test: test object
    """
    sysfs_memory_ksm_path = "/sys/kernel/mm/ksm"
    ksm_params = {}
    ksm_files = eval(params.get('ksm_files'))

    for ksm_file in ksm_files:
        ksm_file_path = os.path.join(sysfs_memory_ksm_path, ksm_file)
        if os.access(ksm_file_path, os.R_OK):
            with open(ksm_file_path, 'r') as fp:
                ksm_params["shm_%s" % ksm_file] = fp.read().strip()
    test.log.debug("Get ksm values from kernel config files:%s", ksm_params)
    return ksm_params


def compare_ksm_parameters(virsh_output, ksm_params, test):
    """
    Compare ksm parameter values

    :param virsh_output: dict, output from virsh node-memory-tune
    :param ksm_params: dict, ksm parameter values
    :param test: test object
    """
    if virsh_output != ksm_params:
        test.fail("Expect virsh numa_memtune output "
                  "is equal to '%s', but "
                  "found '%s'" % (ksm_params, virsh_output))
    test.log.debug("Compare ksm parameters - PASS")


def run_default(params, test):
    """
    Default run function for the test

    :param params: dict, test parameters
    :param test: test object
    """
    def _get_and_compare_ksm():
        ksm_sysfs_config = get_ksm_sysfs_config(params, test)
        ksm_by_virsh = get_ksm_parameters_by_virsh(params, test)

        test.log.info("Step: Compare virsh output to the values set")
        compare_ksm_parameters(ksm_by_virsh, set_ksm_values, test)
        test.log.info("Step: Compare virsh output to sysfs config")
        compare_ksm_parameters(ksm_by_virsh, ksm_sysfs_config, test)

    test.log.info("Step: Compare default virsh node-memory-tune ksm to sysfs config")
    ksm_sysfs_config = get_ksm_sysfs_config(params, test)
    default_ksm_by_virsh = get_ksm_parameters_by_virsh(params, test)
    params['default_ksm_params'] = default_ksm_by_virsh
    compare_ksm_parameters(default_ksm_by_virsh, ksm_sysfs_config, test)
    test.log.info("Step: Set ksm parameters one by one and compare")
    set_ksm_values = eval(params.get('set_ksm_values'))
    for ksm_key, ksm_value in set_ksm_values.items():
        set_ksm_parameters_by_virsh({ksm_key: ksm_value}, test)
    _get_and_compare_ksm()
    test.log.info("Step: Recover to default ksm parameter "
                  "values")
    recover_ksm_params(default_ksm_by_virsh, test)
    test.log.info("Step: Set ksm parameters all in one and compare")
    set_ksm_parameters_by_virsh(set_ksm_values, test)
    _get_and_compare_ksm()


def recover_ksm_params(params, test):
    """
    Recover to default ksm parameter values

    :param params: dict, default ksm parameters
    :param test: test object
    """
    set_ksm_parameters_by_virsh(params, test)
    test.log.debug("Recover to default ksm parameters "
                   "successfully with '%s'", params)


def teardown_default(params, test):
    """
    Default teardown function for the test

    :param params: dict, test parameters
    :param test: test object
    """
    recover_ksm_params(params.get('default_ksm_params'), test)
    test.log.debug("Step: teardown is done")


def run(test, params, env):
    """
    Test node memory tuning

    Get default node memory parameters' values
    Set specified node memory parameters about ksm
    Check virsh node-memory-tune output and corresponding kernel files
    Recover to default node memory parameters' values
    """
    try:
        run_default(params, test)
    finally:
        teardown_default(params, test)
