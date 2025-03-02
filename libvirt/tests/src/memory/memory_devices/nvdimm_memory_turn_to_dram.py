# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0
#   Author: Nannan Li <nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import re

from avocado.utils import distro
from avocado.utils import process

from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml
from virttest import utils_package
from virttest import virsh

from provider.memory import memory_base


def execute_cmd_in_guest(test, session, cmd):
    """
    Execute specific cmd and log output in the guest.

    :params test: test object
    :params session: vm session
    :params cmd: command
    :return output: return cmd result
    """
    ret, output = session.cmd_status_output(cmd)
    test.log.debug(output)
    if ret:
        test.error("Run command failed")

    return output


def run(test, params, env):
    """
    Turned nvdimm to the regular DRAM.
    """
    def setup_test():
        """
        Create nvdimm file on the host.
        """
        process.run(truncate_cmd)

    def run_test():
        """
        Turned nvdimm to the regular DRAM would increase total memory
        """
        test.log.info("TEST_STEP1: Define vm with nvdimm memory")
        memory_base.define_guest_with_memory_device(params, nvdimm_dict, vm_attrs)

        test.log.info("TEST_STEP2: Start guest and Check nvdimm passed in the qemu command line")
        vm.start()
        session = vm.wait_for_login()
        libvirt.check_qemu_cmd_line(eval(expected_qemu_cmdline))

        test.log.info("TEST_STEP3:Check the guest support NVDIMM")
        memory_base.check_boot_config(session, test)

        test.log.info("TEST_STEP4:Login to the guest, check mem size and nvdimm mode")
        vm_mem_before = memory_base.check_used_memory_info(test, session)['total'][0]
        test.log.debug("VM's memory before updating nvdimm to dram: %s", vm_mem_before)

        if not utils_package.package_install('ndctl', session=session):
            test.fail('Cannot install ndctl to vm')
        execute_cmd_in_guest(test, session, cmd=create_namespace)
        execute_cmd_in_guest(test, session, cmd=check_dev)
        ndctl_list = eval(execute_cmd_in_guest(test, session, cmd=list_nvdimm_namespace))
        if ndctl_list[0]['mode'] != devdax_mode:
            test.fail("Expected mode devdax, but %s" % ndctl_list["mode"])
        test.log.debug("Check mode: %s PASS", devdax_mode)

        test.log.info("TEST_STEP5:Turned nvdimm to the regular DRAM, and hot-added memory matches the expectation")
        if not utils_package.package_install('daxctl', session=session):
            test.fail('Cannot install daxctl to vm')
        if distro.detect().name == 'rhel' and int(distro.detect().version) == 9:
            execute_cmd_in_guest(test, session, cmd=persistence_config)
            execute_cmd_in_guest(test, session, cmd=add_kmem)

        execute_cmd_in_guest(test, session, cmd=create_namespace)
        output = execute_cmd_in_guest(test, session, cmd=update_to_dram)
        if not re.findall(r'"mode":"%s"' % dram_mode, output):
            test.fail("Expected mode is dram")
        test.log.debug("Check new mode: %s PASS")

        vm_mem_after = memory_base.check_used_memory_info(test, session)['total'][0]
        test.log.debug("New memory total value is:%s", vm_mem_after)
        if int(vm_mem_after) <= int(vm_mem_before):
            test.fail("Memory should increase after updating nvdimm to dram")
        test.log.debug("Check guest memory increase successfully")

        test.log.info("TEST_STEP6:Used memhog for memory operation")
        execute_cmd_in_guest(test, session, cmd="memhog -r%s 500M" % repeat_times)
        session.close()

        test.log.info("TEST_STEP7:Reboot and check guest status")
        virsh.reboot(vm_name, debug=True)
        if vm.state() != "running":
            test.fail("VM %s should be running, not %s" % (vm.name, vm.state()))
        test.log.debug("Check guest status is running")

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        bkxml.sync()
        process.run("rm -rf %s" % nvdimm_path)

    vm_name = params.get("main_vm")
    original_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = original_xml.copy()
    vm = env.get_vm(vm_name)
    nvdimm_path = params.get("nvdimm_path")
    truncate_cmd = params.get("truncate_cmd")
    expected_qemu_cmdline = params.get("expected_qemu_cmdline")
    nvdimm_dict = eval(params.get("nvdimm_dict"))
    vm_attrs = eval(params.get("vm_attrs", "{}"))
    repeat_times = params.get("repeat_times")

    devdax_mode = params.get("devdax_mode")
    dram_mode = params.get("dram_mode")
    create_namespace = params.get("create_namespace")
    check_dev = params.get("check_dev")
    list_nvdimm_namespace = params.get("list_nvdimm_namespace")
    add_kmem = params.get("add_kmem")
    update_to_dram = params.get("update_to_dram")
    persistence_config = params.get("persistence_config")

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
