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

from virttest import virsh
from virttest import utils_misc
from virttest import utils_net
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.virtual_network import pktgen_utils
from provider.virtual_network import network_base
from provider.interface import interface_base


def write_to_result_file(test, params):
    """
    Write some qemu, kvm version info and write them into pktgen test result.

    :params, params
    :return result_file, pktgen test result file.
    """
    kvm_ver_chk_cmd = params.get("kvm_ver_chk_cmd")
    libvirt_ver_chk_cmd = params.get("libvirt_ver_chk_cmd")

    result_path = utils_misc.get_path(test.resultsdir, "pktgen_perf.RHS")
    result_file = open(result_path, "w")
    kvm_ver = process.system_output(kvm_ver_chk_cmd, shell=True).decode()
    libvirt_ver = process.system_output(libvirt_ver_chk_cmd, shell=True).decode()
    host_ver = os.uname().release
    result_file.write("### kvm-userspace-ver : %s\n" % kvm_ver)
    result_file.write("### libvirt-userspace-ver : %s\n" % libvirt_ver)
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
        recreate_serial_console = False
        # vhost-vdpa needs XML updates before starting VM
        if params.get("dev_type") == "vdpa" and params.get("iface_dict"):
            # Ensure VM is stopped before XML changes
            virsh.destroy(vm_name, debug=True, ignore_status=True)
            recreate_serial_console = True

            libvirt_vmxml.remove_vm_devices_by_type(vm, "interface")
            vm_attrs = eval(params.get("vm_attrs", "{}"))
            if vm_attrs:
                vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
                vmxml.setup_attrs(**vm_attrs)
                vmxml.sync()

            # Add a single vhost-vdpa interface
            vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
            iface_dict = interface_base.parse_iface_dict(params)
            iface_dev = interface_base.create_iface(params.get("dev_type"), iface_dict)
            libvirt.add_vm_device(vmxml, iface_dev)

            if vm.virtnet:
                vm.virtnet[0].mac = params.get("mac_addr")
                vm.virtnet[0].nettype = "vdpa"

        if not vm.is_alive():
            vm.start()
        test.log.debug("Test with Guest xml:%s", vm_xml.VMXML.new_from_dumpxml(vm_name))
        session = vm.wait_for_serial_login(
            restart_network=True, recreate_serial_console=recreate_serial_console
        )
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
        if params.get("dev_type") == "vdpa":
            # vhost-vdpa may not have a guest IP (ARP/arping), so vm.verify_dmesg()
            # can fail by attempting a network login.
            utils_misc.verify_dmesg(
                ignore_result=False,
                session=session,
            )
        else:
            vm.verify_dmesg()
        vm.verify_kernel_crash()

        result_file.close()

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    flush_firewall_rules_cmd = params.get("flush_firewall_rules_cmd")
    guest_ver_cmd = params.get("guest_ver_cmd")
    test_vm = params.get_boolean("test_vm")
    host_ver = os.uname().release
    params_for_ip = params.copy()
    if params.get("netdst") == "private":
        params_for_ip["netdst"] = params.get("priv_brname", "atbr0")
    ips = {'host_ip': utils_net.get_host_ip_address(params_for_ip)}

    run_test()
