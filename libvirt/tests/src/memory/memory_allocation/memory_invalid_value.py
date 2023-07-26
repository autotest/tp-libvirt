
from virttest import virsh
from virttest import libvirt_version

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_vmxml


def run(test, params, env):
    """
    Verify libvirt forbids invalid memory value

    1.value: Zero/Minus
    2.mem type: Memory/currentMemory/maxMemory/numa
                mem currentMem numa mixed
    """
    def setup_test():
        pass

    def run_test():
        """
        Define vm and check msg
        Start vm and check config
        """
        test.log.info("TEST_STEP1: Define vm and check result")
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        vmxml.setup_attrs(**vm_attrs)
        test.log.debug("Define vm with %s." % vmxml)
        cmd_result = virsh.define(vmxml.xml, debug=True)
        if num == "minus":
            libvirt.check_result(cmd_result, minus_error_msg)
        elif num == "zero":
            if mem_config == "mam_memory":
                if libvirt_version.version_compare(9, 5, 0):
                    libvirt.check_result(cmd_result, error_msg_2)
                else:
                    libvirt.check_result(cmd_result, error_msg)
            elif mem_config == "memory":
                libvirt.check_result(cmd_result, error_msg)
            else:
                test.log.info("TEST_STEP2: Check xml after define")
                check_mem_config()
                test.log.info("TEST_STEP3: Check start vm get correct result")
                res = virsh.start(vm_name)
                libvirt.check_exit_status(res, start_vm_error)
                if start_error_msg:
                    libvirt.check_result(res, start_error_msg)

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        bkxml.sync()

    def check_mem_config():
        """
        Check current mem device config
        """
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        libvirt_vmxml.check_guest_xml_by_xpaths(vmxml, xpaths_list)

    vm_name = params.get("main_vm")
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    error_msg = params.get("error_msg", "")
    error_msg_2 = params.get("error_msg_2", "")
    minus_error_msg = params.get("minus_error_msg", "")
    start_error_msg = params.get("start_error_msg", "")

    num = params.get("num")
    mem_config = params.get("mem_config")
    xpaths_list = eval(params.get("xpaths_list", '{}'))
    vm_attrs = eval(params.get("vm_attrs"))
    start_vm_error = "yes" == params.get("start_vm_error", "no")

    try:
        if num == "zero" and mem_config == "numa":
            libvirt_version.is_libvirt_feature_supported(params)
        setup_test()
        run_test()

    finally:
        teardown_test()
