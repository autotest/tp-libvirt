#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Red Hat
#
#   SPDX-License-Identifier: GPL-2.0
#
#   Author: Liang Cong <lcong@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import ast

from avocado.utils import cpu as cpu_utils
from avocado.utils import process

from virttest import cpu
from virttest import libvirt_version
from virttest import utils_misc
from virttest import utils_package
from virttest.libvirt_xml import domcapability_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Verify below secure launch features are supported by the host:
    1. AMD SEV, SEV-ES, SEV-SNP
    2. INTEL SGX
    3. INTEL TDX
    """
    def validate_prerequisites():
        """
        Verify secure launch prerequisites of the host.
        """
        def _validate_cpu_vendor():
            """
            Validate host CPU vendor matches the expected vendor specified
            """
            exp_vendor = params.get("cpu_vendor")
            act_vendor = cpu_utils.get_vendor()
            if exp_vendor and exp_vendor != act_vendor:
                test.cancel("%s is not supported on %s CPU." % (secure_feature, act_vendor))

        def _validate_cpu_flag():
            """
            Validate host CPU supports the required feature flags
            """
            host_cpu_flags = set(cpu.get_cpu_flags())
            cpu_flags = ast.literal_eval(params.get("cpu_flags", "[]"))
            if cpu_flags and not set(cpu_flags).issubset(host_cpu_flags):
                test.cancel("%s requires cpu flags %s, but host cpu flags is %s." % (secure_feature, cpu_flags, host_cpu_flags))

        def _validate_kernel_option():
            """
            Validate required kernel options are present in kernel cmdline
            """
            req_kernel_opts = ast.literal_eval(params.get("req_kernel_opts", "[]"))
            ker_cml = utils_misc.get_ker_cmd()
            if not all(opt in ker_cml for opt in req_kernel_opts):
                test.cancel("%s requires kernel opt %s, but kernel cml is %s." % (secure_feature, req_kernel_opts, ker_cml))

        secure_feature = params.get("secure_feature")
        _validate_cpu_vendor()
        _validate_cpu_flag()
        _validate_kernel_option()

    def setup_test():
        """
        Setup the environment:
        1. install require packages
        """
        if validate_cmds:
            req_pkgs = [cmd_info["package"] for cmd_info in validate_cmds.values()]
            if not utils_package.package_install(req_pkgs):
                test.error("Fail to install packages '%s'" % req_pkgs)

    def run_test():
        """
        Test steps
        1. Check expected kernel msg
        2. Check specific tool output
        3. Check virsh domcapabilities output
        """
        test.log.info("TEST_STEP1: Check expected kernel msg")
        kernel_msg_cmd = params.get("kernel_msg_cmd")
        kernel_msg_patterns = ast.literal_eval(
            params.get("kernel_msg_patterns", "[]"))
        result = process.run(kernel_msg_cmd, shell=True)
        libvirt.check_result(result, expected_match=kernel_msg_patterns)

        test.log.info("TEST_STEP2: Check specific tool output")
        if validate_cmds:
            for cmd, cmd_info in validate_cmds.items():
                pkg_patterns = ast.literal_eval(
                    params.get("%s_patterns" % cmd.replace("-", "_"), "[]"))
                if pkg_patterns:
                    pkg_cmd = "%s %s" % (cmd, cmd_info["cmd_params"])
                    result = process.run(pkg_cmd, shell=True)
                    for p_pattern in pkg_patterns:
                        libvirt.check_result(result, expected_match=p_pattern)

        test.log.info("TEST_STEP3: Check virsh domcapabilities output")
        domcap_xpath = ast.literal_eval(params.get("domcap_xpath", "[]"))
        if domcap_xpath:
            domcap_xml = domcapability_xml.DomCapabilityXML()
            libvirt_vmxml.check_guest_xml_by_xpaths(domcap_xml, domcap_xpath)

    libvirt_version.is_libvirt_feature_supported(params)
    validate_cmds = ast.literal_eval(params.get("validate_cmds", "{}"))

    validate_prerequisites()
    setup_test()
    run_test()
