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
import json

from avocado.utils import memory as avocado_mem

from virttest import libvirt_version
from virttest import virsh
from virttest import utils_misc
from virttest import utils_sys
from virttest.libvirt_xml import domcapability_xml, vm_xml
from virttest.staging import utils_memory
from virttest.utils_libvirt import libvirt_bios, libvirt_memory, libvirt_vmxml


def run(test, params, env):
    """
    Life cycle test of a TDX guest with various memory configs
    """
    def setup_test():
        """
        Test setup:
        1. Set up hugepage
        2. Prepare the guest xml
        """
        if hugepage_memory is not None:
            test.log.info("TEST_SETUP: Set up hugepage")
            default_pagesize = avocado_mem.get_huge_page_size()
            hp_num = int(hugepage_memory) // default_pagesize
            utils_memory.set_num_huge_pages(hp_num)

        test.log.info("TEST_SETUP: Prepare the guest xml")
        vmxml.os = libvirt_bios.remove_bootconfig_items_from_vmos(vmxml.os)
        vm_attrs.update(os_firmware_dict)
        vmxml.setup_attrs(**vm_attrs)

    def _check_tdx_guest(guest_vm):
        """
        Verify the TDX environment inside the guest OS

        :param guest_vm: The VM object
        """
        dmesg_pattern = params.get("dmesg_pattern")
        tdx_device_path = params.get("tdx_device_path")
        with guest_vm.wait_for_login() as session:
            # Check dmesg for TDX initialization
            if not utils_sys.check_dmesg_output(dmesg_pattern, True, session):
                test.fail("Dmesg check for %s failed in guest" % dmesg_pattern)
            # Check TDX device existence
            if not utils_misc.check_exists(tdx_device_path, session):
                test.fail("TDX device at %s doesn't exist in guest" % tdx_device_path)
            # Memory consumption check
            status, output = libvirt_memory.consume_vm_freememory(session)
            if status:
                test.fail("Fail to consume guest memory. Output:%s" % output)

    def _check_tdx_via_qmp(guest_name):
        """
        Verifies TDX guest properties via QMP commands

        :param guest_name: Name of VM
        """
        qmp_expects_dict = ast.literal_eval(params.get("qmp_expects_dict", "{}"))
        qmp_cmd = params.get("qmp_cmd")
        for prop, exp in qmp_expects_dict.items():
            f_qmp_cmd = qmp_cmd % prop
            res = virsh.qemu_monitor_command(guest_name, f_qmp_cmd, debug=True).stdout_text
            actual = json.loads(res)['return']
            if exp != actual:
                test.fail(
                    "QMP cmd '%s' result '%s' doesn't contain expected return value %s"
                    % (f_qmp_cmd, res, exp)
                )

    def _check_tdx_support():
        """
        Validate host TDX support via libvirt domcapabilities
        """
        domcap_xpath = ast.literal_eval(params.get("domcap_xpath", "[]"))
        if domcap_xpath:
            domcap_xml = domcapability_xml.DomCapabilityXML()
            if not libvirt_vmxml.check_guest_xml_by_xpaths(domcap_xml, domcap_xpath, True):
                test.cancel("Host does not support required TDX features. "
                            "Domcapabilities check failed for XPaths: %s. "
                            "Domcapabilities xml is: %s" % (domcap_xpath, domcap_xml))

    def run_test():
        """
        Test steps
        1. Define the guest
        2. Start the guest
        3. Initial check TDX guest
        4. Lifecycle operations of TDX guest
        """
        test.log.info("TEST_STEP1: Define the guest")
        vmxml.sync()
        cur_xml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        test.log.debug("Guest config xml before start:\n%s", cur_xml)

        test.log.info("TEST_STEP2: Start the guest")
        vm.start()
        cur_xml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        test.log.debug("Guest config xml after start is:\n%s", cur_xml)

        test.log.info("TEST_STEP3: Initial check TDX guest")
        _check_tdx_guest(vm)
        _check_tdx_via_qmp(vm_name)

        test.log.info("TEST_STEP4: Lifecycle operations of TDX guest")
        operations = [
            ("Pause/Resume", lambda: [vm.pause(), vm.resume()]),
            ("Reboot", lambda: vm.reboot()),
            ("Shutdown/Start", lambda: [vm.shutdown(), vm.wait_for_shutdown(), vm.start()]),
            ("Reset", lambda: virsh.reset(vm.name, ignore_status=False, debug=True))
        ]
        for op_name, op_func in operations:
            test.log.info("Performing operation: %s", op_name)
            op_func()
            _check_tdx_guest(vm)
            _check_tdx_via_qmp(vm_name)

    libvirt_version.is_libvirt_feature_supported(params)
    _check_tdx_support()
    vm_name = params.get("main_vm")
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    vm = env.get_vm(vm_name)

    os_firmware_dict = ast.literal_eval(params.get("os_firmware_dict", "{}"))
    vm_attrs = ast.literal_eval(params.get("vm_attrs"))
    hugepage_memory = params.get("hugepage_memory")

    try:
        setup_test()
        run_test()
    finally:
        test.log.info("TEST_TEARDOWN: Clean up environment.")
        if hugepage_memory is not None:
            utils_memory.set_num_huge_pages(0)
        bkxml.sync()
