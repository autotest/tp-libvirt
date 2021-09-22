import platform
import re

from virttest import virsh

from virttest.utils_test import libvirt


def substitute_param(params):
    """
    Substitute specified parameters with necessary information

    :param params: dict, parameters used
    :return: str, the parameter after substituted
    """

    baseline_option = params.get('hypv_cpu_baseline_option')
    machine_type = params.get("machine_type")
    baseline_option = re.sub(r"--arch %s", "--arch %s" % platform.machine().lower(), baseline_option)
    baseline_option = re.sub(r"--machine %s", "--machine %s" % machine_type, baseline_option)
    return baseline_option


def run(test, params, env):
    """
    Run tests for virsh hypervisor-cpu-baseline with parameters
    """
    baseline_option = substitute_param(params)
    domcap_path = params.get("domcap_path")
    err_msg = params.get("err_msg")

    ret = virsh.hypervisor_cpu_baseline(domcap_path,
                                        options=baseline_option,
                                        ignore_status=True,
                                        debug=True)
    libvirt.check_result(ret, expected_fails=err_msg)
