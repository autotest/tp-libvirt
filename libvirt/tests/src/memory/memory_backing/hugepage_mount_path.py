# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Nannan Li <nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import re

from avocado.utils import process

from virttest import utils_libvirtd
from virttest import utils_misc
from virttest import virsh
from virttest import test_setup

from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices import memory
from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_memory
from virttest.staging import service

from provider.memory import memory_base


def run(test, params, env):
    """
    Verify the mount path config takes effect
    Verify huge page path mount doesn't impact running guest vm
    """
    def setup_test():
        """
        Set hugepage on host and prepare guest
        """
        test.log.info("TEST_SETUP: Set hugepage on host and prepare guest")
        memory_base.check_mem_page_sizes(
            test, default_page_size, int(set_pagesize_2), [int(set_pagesize_1)])
        hp_cfg = test_setup.HugePageConfig(params)
        if case == "customized":
            process.run("umount %s" % default_path, ignore_status=True)
            hp_cfg.mount_hugepage_fs()
        hp_cfg.set_kernel_hugepages(set_pagesize_1, set_pagenum_1)
        hp_cfg.set_kernel_hugepages(set_pagesize_2, set_pagenum_2)

        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        vmxml.setup_attrs(**vm_attrs)
        virsh.define(vmxml.xml, debug=True, ignore_status=False)
        test.log.debug("Define xml by :\n%s ", vmxml)

    def run_test():
        """
        Define guest, start guest and check mem.
        """
        test.log.info("TEST_STEP1: Modify the huge page mount path config ")
        if case != "default":
            qemu_obj = libvirt.customize_libvirt_config(
                {"hugetlbfs_mount": '"%s"' % hugetlbfs_mount}, "qemu",
                restart_libvirt=False)
            params.update({"qemu_conf_obj": qemu_obj})
        process.run("cat /etc/libvirt/qemu.conf", shell=True)

        test.log.info("TEST_STEP2: Restart libvirt service")
        libvirtd = utils_libvirtd.Libvirtd()
        libvirtd.restart(wait_for_start=False)

        def _compare_state():
            service_manager = service.Service(libvirtd.service_name)
            res = service_manager.status()
            return res == expect_active

        if not utils_misc.wait_for(lambda: _compare_state(), timeout=20):
            test.fail("Expect active service should be:'%s'" % expect_state)
        else:
            test.log.debug("Get expected service status: '%s'" % expect_state)
        if case == "disabled":
            return

        test.log.info("TEST_STEP3: Start guest")
        vm.start()
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        test.log.debug("Current vmxml is:\n %s", vmxml)

        test.log.info("TEST_STEP4: Check the qemu cmd line")
        libvirt.check_qemu_cmd_line(check_qemu)

        test.log.info("TEST_STEP5: Check security context")
        cmd_result = process.run(check_security_cmd, ignore_status=True,
                                 shell=True).stdout_text
        if not re.search(security_context, cmd_result):
            test.fail("Expect get %s in %s" % (security_context, cmd_result))

        test.log.info("TEST_STEP6: Check the guest log")
        libvirt.check_logfile(err_1, log_path % vm_name, str_in_log=False)
        libvirt.check_logfile(err_2, log_path % vm_name, str_in_log=False)

        if case == "customized":
            return

        test.log.info("TEST_STEP7: Stop services")
        utils_libvirtd.Libvirtd().stop()

        test.log.info("TEST_STEP8: Mount hugepage path")
        hp_cfg = test_setup.HugePageConfig(params)
        hp_cfg.hugepage_size = mount_pagesize
        hp_cfg.mount_hugepage_fs()

        test.log.info("TEST_STEP9: Do virsh list to check guest status")
        output = virsh.dom_list("--all", debug=True).stdout.strip()
        result = re.search(r".*%s.*%s.*" % (vm_name, "running"), output)
        if not result:
            test.fail("VM '{}' with the state running is not found".format(vm_name))

        test.log.info("TEST_STEP10: Attach memory device")
        mem_dev = memory.Memory()
        mem_dev.setup_attrs(**attach_dict)
        virsh.attach_device(vm.name, mem_dev.xml, debug=True,
                            ignore_status=False)

        test.log.info("TEST_STEP11: Check mem")
        session = vm.wait_for_login()
        libvirt_memory.consume_vm_freememory(session, consume_value)
        session.close()

        test.log.info("TEST_STEP12: Destroy the guest")
        virsh.destroy(vm_name, ignore_status=False)

        test.log.info("TEST_STEP13: Restart service")
        libvirtd = utils_libvirtd.Libvirtd(all_daemons=True)
        libvirtd.restart(wait_for_start=False)
        if not utils_misc.wait_for(lambda: _compare_state(), timeout=20):
            test.fail("Expect service state: '%s'" % expect_state)
        else:
            test.log.debug("Get expected '%s' service", expect_state)

        test.log.info("TEST_STEP14: Start the guest")
        virsh.start(vm_name, ignore_status=False)

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        libvirt.customize_libvirt_config(
            None, "qemu", config_object=params.get("qemu_conf_obj"),
            is_recover=True)
        bkxml.sync()

        hp_cfg = test_setup.HugePageConfig(params)
        hp_cfg.cleanup()
        if default_path:
            hp_cfg.hugepage_path = default_path
            hp_cfg.mount_hugepage_fs()

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    log_path = params.get("log_path")
    err_1 = params.get("guest_log_error1")
    err_2 = params.get("guest_log_error2")
    hugetlbfs_mount = params.get("hugetlbfs_mount")
    default_path = params.get("default_path")
    consume_value = int(params.get("consume_value"))

    default_page_size = int(params.get('default_page_size'))
    mount_pagesize = params.get("mount_pagesize", '{}')
    set_pagesize_1 = params.get("set_pagesize_1")
    set_pagenum_1 = params.get("set_pagenum_1")
    set_pagesize_2 = params.get("set_pagesize_2")
    set_pagenum_2 = params.get("set_pagenum_2")

    case = params.get("case")
    check_security_cmd = params.get("check_security_cmd")
    check_qemu = eval(params.get("check_qemu", '{}'))
    security_context = params.get("security_context")
    expect_active = bool(params.get("expect_active"))
    expect_state = params.get("expect_state")
    attach_dict = eval(params.get("attach_dict", "{}"))
    vm_attrs = eval(params.get("vm_attrs", "{}"))

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
