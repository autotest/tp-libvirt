# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Nan Li <nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import os

from avocado.utils import process

from virttest import virsh
from virttest import virt_vm

from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.console import Console

from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Test seclabel with file type console device
    """

    def run_test():
        """
        1) Define guest with seclabel.
        2) Start guest.
        3) Attach console with seclabel.
        """
        test.log.info("TEST_STEP1: Define guest")
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        console_obj = Console()
        console_obj.setup_attrs(**console_dict)

        if need_define:
            vmxml.devices = vmxml.devices.append(console_obj)
            test.log.debug("Define xml by \n:%s", vmxml)
            cmd_result = virsh.define(vmxml.xml, debug=True)
            if define_error:
                libvirt.check_result(cmd_result, define_error)
                return
            else:
                libvirt.check_exit_status(cmd_result)

        test.log.info("TEST_STEP2: Start guest")
        if need_start:
            try:
                vm.start()
                if model == "dac":
                    if not vm.is_alive():
                        test.fail("Guest state should be running")
                    if not os.path.exists(log_path):
                        test.fail("%s should exist", log_path)
                    return
            except virt_vm.VMStartError as e:
                if start_error and start_error not in str(e):
                    test.fail("Not get starting VM error '%s' in %s" % (
                        start_error, str(e)))
                return

        test.log.info("TEST_STEP3: Attach a console device.")
        process.run('touch %s' % log_path)
        res = virsh.attach_device(vm_name, console_obj.xml, debug=True)
        if attached_error:
            libvirt.check_result(res, expected_fails=attached_error)

    def teardown_test():
        """
        Clean data.
        """
        if vm.is_alive():
            vm.destroy(gracefully=False)
        bkxml.sync()

        if os.path.exists(log_path):
            os.remove(log_path)

    vm_name = params.get("main_vm")
    console_dict = eval(params.get('device_dict', '{}'))

    model = params.get("model")
    define_error = params.get('define_error', '')
    start_error = params.get('start_error', '')
    log_path = params.get('log_path')
    need_define = "yes" == params.get('need_define')
    need_start = "yes" == params.get('need_start')
    attached_error = "yes" == params.get('attached_error')

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        run_test()

    finally:
        teardown_test()
