# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0
#   Author: Nannan Li<nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import os
import time

from avocado.utils import process

from virttest import utils_package
from virttest import virsh
from virttest import utils_net
from virttest import utils_misc
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml

from provider.virtual_network import network_base

virsh_opt = {"debug": True, "ignore_status": False}


def transfer_file(vm_object, source_path):
    """
    Transfer stress file to guest.

    :params vm_object, vm object
    :params source_path, the source path that's transferred to guest.
    :return dest_path, destination path in guest.
    """
    if not vm_object.is_alive():
        virsh.start(vm_object.name)
    session = vm_object.wait_for_login()

    dest_path = "/var/%s" % os.path.basename(source_path)
    vm_object.copy_files_to(
        host_path=source_path, guest_path=dest_path)

    def _file_exists():
        if not session.cmd("cat %s" % dest_path, ignore_all_errors=True):
            return True
    utils_misc.wait_for(lambda: _file_exists(), 15)

    session.close()

    return dest_path


def run(test, params, env):
    """
    Packet buffer stress test for user - only for linux.
    """
    def setup_test():
        """
        Prepare the test environment with required guest type and interface.

        :return vm_list: list of vm names.
        """
        test.log.info("TEST_SETUP: Prepare specific guest os version and interface.")
        vm_list = network_base.prepare_vms_with_iface(
            params, vm_image_path=vm_image_paths, iface_list=iface_list)

        return vm_list

    def run_test(vm_list):
        """
        Set MTU value in guest interface and run stress test.
        """
        for vm_name in vm_list:
            test.log.debug("TEST_STEP: Transfer stress file to guest: %s", vm_name)
            env.create_vm(vm_type='libvirt', target="", name=vm_name, params=params, bindir=test.bindir)
            vm = env.get_vm(vm_name)
            dest_path = transfer_file(vm, stress_file)

            test.log.debug("TEST_STEP: Replace interface with %s type" % iface_type)
            session = vm.wait_for_login(timeout=360)
            iface_name = utils_net.get_linux_ifname(session=session)[0]
            iface_mac = utils_net.get_linux_mac(session, iface_name)
            virsh.detach_interface(vm_name, "--type %s --mac %s" % (basic_iface_type, iface_mac),
                                   flagstr='--live', **virsh_opt)

            iface = libvirt_vmxml.create_vm_device_by_type('interface', iface_list_2)
            virsh.attach_device(vm.name, iface.xml, flagstr='--config', **virsh_opt)
            test.log.debug("Test with vm xml:%s", vm_xml.VMXML.new_from_dumpxml(vm_name))
            session.close()

            test.log.debug("TEST_STEP: Set MTU value in guest: %s", vm_name)
            virsh.destroy(vm_name, **virsh_opt)
            virsh.start(vm_name, **virsh_opt)
            session = vm.wait_for_login(timeout=360)
            network_base.set_guest_iface_mtu(test, session, mtu=net_mtu)

            test.log.debug("TEST_STEP: Compile and execute the stress script.")
            utils_package.package_install(["gcc"], session, 360)
            session.cmd("gcc %s -o %s" % (dest_path, tmp_exe_file))
            session.sendline("%s &" % tmp_exe_file)
            time.sleep(60)

            result = process.getstatusoutput(
                "ps -aux|grep /usr/bin/gnome-calculator |grep -v grep",
                timeout=60,
                shell=True,
            )[0]
            if result == 0:
                test.fail("Virtual machine has security issues")
            session.send_ctrl("^c")
            test.log.info("send ctrl+c command to exit the current process.")

            test.log.debug("TEST_STEP: Check guest work normally and no crash occurred.")
            vm.verify_kernel_crash()
            vm.verify_dmesg()

    def teardown_test(vm_list):
        """
        Clean data.
        """
        test.log.debug("TEST_TEARDOWN: Recover the env.")
        if vm_list:
            for vm_name in vm_list:
                if vm_name != params.get("main_vm"):
                    virsh.destroy(vm_name)
                    virsh.undefine(vm_name, options='--nvram')

    iface_type = params.get("iface_type")
    basic_iface_type = params.get("basic_iface_type", "network")

    iface_list = eval(params.get('iface_list', '[]'))
    iface_list_2 = eval(params.get('iface_list_2', '{}'))
    vm_image_paths = eval(params.get('vm_image_paths', '{}'))
    net_mtu = params.get('net_mtu')
    stress_file = params.get('stress_file')
    stress_file = os.path.join(os.path.dirname(__file__), stress_file)
    tmp_exe_file = params.get("tmp_exe_file", "/tmp/test")
    vm_list = []

    try:

        vm_list = setup_test()
        run_test(vm_list)

    finally:
        teardown_test(vm_list)
