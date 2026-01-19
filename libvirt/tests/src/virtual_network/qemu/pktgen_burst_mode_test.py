# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0
#   Author: Nannan Li<nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

import os

from avocado.utils import distro
from avocado.utils import process

from virttest import utils_misc
from virttest import utils_net
from virttest.libvirt_xml import vm_xml

from provider.virtual_network import pktgen_utils
from provider.virtual_network import network_base


def write_to_result_file(test, params):
    """
    Write some qemu, kvm version info and write them into pktgen test result.

    :params, params
    :return result_file, pktgen test result file.
    """
    kvm_ver_chk_cmd = params.get("kvm_ver_chk_cmd")

    result_path = utils_misc.get_path(test.resultsdir, "pktgen_perf.RHS")
    result_file = open(result_path, "w")
    kvm_ver = process.system_output(kvm_ver_chk_cmd, shell=True).decode()
    host_ver = os.uname().release
    result_file.write("### kvm-userspace-ver : %s\n" % kvm_ver)
    result_file.write("### kvm_version : %s\n" % host_ver)

    return result_file


def run(test, params, env):
    """
    Pktgen tests with burst mode.
    """
    def run_test():
        """
        Run pktgen in burst mode for vm.
        """
        test.log.debug("TEST_STEP 1: Prepare qemu version into pktgen test result")
        result_file = write_to_result_file(test, params)
        if flush_firewall_rules_cmd:
            process.system(flush_firewall_rules_cmd, shell=True)

        _distro = distro.detect()
        if _distro.name == 'rhel' and int(_distro.version) > 7:
            is_64k_page_size = "64k" in host_ver
            if is_64k_page_size:
                pktgen_utils.install_package(host_ver, pagesize="64k")
            else:
                pktgen_utils.install_package(host_ver)

        test.log.debug("TEST_STEP 2: Test with guest and host connectivity")
        if not vm.is_alive():
            vm.start()
        test.log.debug("Test with Guest xml:%s", vm_xml.VMXML.new_from_dumpxml(vm_name))
        session = vm.wait_for_serial_login(restart_network=True)
        network_base.ping_check(params, ips, session, force_ipv4=True)

        test.log.debug("TEST_STEP 3: Install package and Run pktgen in burst mode.")
        guest_ver = session.cmd_output(guest_ver_cmd)
        result_file.write("### guest-kernel-ver :%s" % guest_ver)
        if _distro.name == 'rhel' and int(_distro.version) > 7:
            guest_is_64k_page_size = "64k" in guest_ver
            if guest_is_64k_page_size:
                pktgen_utils.install_package(
                    guest_ver.strip(),
                    pagesize="64k",
                    vm=vm,
                    session_serial=session)
            else:
                pktgen_utils.install_package(
                    guest_ver.strip(), vm=vm, session_serial=session)

        pktgen_utils.run_tests_for_category(
            params, result_file, test_vm, vm, session)

        test.log.debug("TEST_STEP: Check the guest dmesg without error messages.")
        vm.verify_dmesg()
        vm.verify_kernel_crash()

        result_file.close()

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    flush_firewall_rules_cmd = params.get("flush_firewall_rules_cmd")
    guest_ver_cmd = params.get("guest_ver_cmd")
    test_vm = params.get_boolean("test_vm")
    host_ver = os.uname().release
    ips = {'host_ip': utils_net.get_host_ip_address(params)}

    run_test()
