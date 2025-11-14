# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0
#   Author: Nannan Li<nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

import os

from avocado.utils import process

from virttest import utils_misc
from virttest import utils_net
from virttest import virsh
from virttest.libvirt_xml import vm_xml

from provider.virtual_network import pktgen_utils
from provider.virtual_network import network_base


def write_in_result_file(test, params):
    """
    Write some qemu, kvm version info and write them into pktgen test result.

    :params, params
    :return result_file, pktgen test result file.
    """
    kvm_ver_chk_cmd = params.get("kvm_ver_chk_cmd")

    result_path = utils_misc.get_path(test.resultsdir, "pktgen_perf.RHS")
    result_file = open(result_path, "w")
    kvm_ver = process.system_output(kvm_ver_chk_cmd, shell=True).decode()
    host_ver = os.uname()[2]
    result_file.write("### kvm-userspace-ver : %s\n" % kvm_ver)
    result_file.write("### kvm_version : %s\n" % host_ver)

    return result_file


def run(test, params, env):
    """
    Pktgen tests with burst mode.
    """
    def setup_test():
        """
        Prepare the test environment with required guest type and interface.

        """
        utils_net.create_linux_bridge_tmux(bridge_name, host_iface)
        if params.get_boolean("pre_tap"):
            network_base.create_tap(tap_name, bridge_name, 'root', flag=tap_flag)

        test.log.info("TEST_SETUP: guest and interface.")
        network_base.prepare_single_vm(params, vm_name, iface_list)

    def run_test():
        """
        Run pktgen in burst mode for vm.
        """
        result_file = write_in_result_file(test, params)

        if disable_iptables_rules_cmd:
            process.system(disable_iptables_rules_cmd, shell=True)

        pktgen_runner = pktgen_utils.PktgenRunner()
        if pktgen_runner.is_version_lt_rhel(process.getoutput("uname -r"), 7):
            if host_ver.count("64k"):
                pktgen_runner.install_package(host_ver, pagesize="64k")
            else:
                pktgen_runner.install_package(host_ver)

        test.log.debug("TEST_STEP: Test with guest: %s.", vm_name)
        env.create_vm(vm_type='libvirt', target="", name=vm_name, params=params, bindir=test.bindir)
        vm = env.get_vm(vm_name)
        vm.start()
        test.log.debug("Guest xml:%s", vm_xml.VMXML.new_from_dumpxml(vm_name))

        test.log.debug("TEST_STEP: Test with guest and host connectivity")
        session_serial = vm.wait_for_serial_login(restart_network=True)
        mac = vm.get_mac_address()
        params.update({
            'vm_ping_host': 'pass'}
        )
        ips = {
            'vm_ip': utils_net.get_guest_ip_addr(session_serial, mac),
            'host_ip': host_ip
        }
        network_base.ping_check(params, ips, session_serial, force_ipv4=True)

        test.log.debug("TEST_STEP: Install package and Run pktgen in burst mode.")
        guest_ver = session_serial.cmd_output(guest_ver_cmd)
        result_file.write("### guest-kernel-ver :%s" % guest_ver)
        if pktgen_runner.is_version_lt_rhel(session_serial.cmd("uname -r"), 7):
            if guest_ver.count("64k"):
                pktgen_runner.install_package(
                    guest_ver.strip(),
                    pagesize="64k",
                    vm=vm,
                    session_serial=session_serial)
            else:
                pktgen_runner.install_package(
                    guest_ver.strip(), vm=vm, session_serial=session_serial)

        pktgen_utils.run_tests_for_category(
            params, result_file, test_vm, vm, session_serial)

        test.log.debug("TEST_STEP: Check the guest dmesg without error messages.")
        vm.verify_dmesg()
        vm.verify_kernel_crash()

        virsh.destroy(vm_name)
        virsh.undefine(vm_name, options='--nvram')

        result_file.close()

    def teardown_test():
        """
        Clean data.
        """
        test.log.debug("TEST_TEARDOWN: Recover the env.")
        network_base.delete_tap(tap_name)
        utils_net.delete_linux_bridge_tmux(bridge_name, host_iface)

        backup_vmxml.sync()

    vm_name = params.get("main_vm")
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    backup_vmxml = vmxml.copy()

    disable_iptables_rules_cmd = params.get("disable_iptables_rules_cmd")
    guest_ver_cmd = params["guest_ver_cmd"]
    test_vm = params.get_boolean("test_vm")
    host_ver = os.uname()[2]

    rand_id = utils_misc.generate_random_string(3)
    bridge_name = 'br_' + rand_id
    tap_name = 'tap_' + rand_id
    host_iface = utils_net.get_default_gateway(iface_name=True, force_dhcp=True, json=True)
    host_ip = utils_net.get_ip_address_by_interface(host_iface, ip_ver='ipv4')

    iface_list = eval(params.get('iface_list', '{}'))
    tap_flag = params.get("tap_flag")

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
