import logging
import re

from avocado.utils import process

from virttest import virsh
from virttest import virt_vm
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Test virtual L3 cache as follows.
    1) Start vm with virtual L3 cache and none cpu mode
    2) Start vm with virtual L3 cache and host-passthrough cpu mode
    3) Start vm with virtual L3 cache and host-model cpu mode
    4) Invalid virtual L3 cache info for VM

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """

    def create_cpu_xml():
        """
        Create cpu xml

        :return: cpu object
        """
        cpu_xml = vm_xml.VMCPUXML()
        cache_attrs = {}
        if cpu_mode:
            cpu_xml.mode = cpu_mode
        if cache_level:
            cache_attrs.update({'level': cache_level})
        if cache_mode:
            cache_attrs.update({'mode': cache_mode})
        cpu_xml.cache = cache_attrs
        logging.debug(cpu_xml)
        return cpu_xml

    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)

    cpu_mode = params.get('cpu_mode')
    cache_level = params.get('cache_level')
    cache_mode = params.get('cache_mode')
    cmd_in_vm = params.get('cmd_in_vm')
    qemu_line = params.get('qemu_line')
    cmd_output_regex = params.get('cmd_output_regex')
    string_in_cmd_output = "yes" == params.get("string_in_cmd_output", "no")
    status_error = "yes" == params.get("status_error", "no")
    err_msg = params.get("err_msg")

    qemu_lines = []
    if qemu_line:
        qemu_lines.append(qemu_line)
    bkxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    try:
        # Create cpu xml for test
        cpu_xml = create_cpu_xml()

        # Update vm's cpu
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        vmxml.cpu = cpu_xml

        # Update for "passthrough" test
        if cpu_mode == "host-passthrough":
            qemu_line_cacheinfo = params.get("qemu_line_cacheinfo",
                                             "host-cache-info=off")
            qemu_lines.append(qemu_line_cacheinfo)
            if cache_mode == "passthrough" and cmd_in_vm:
                cmd_output_regex = process.run(cmd_in_vm, shell=True).stdout_text

        if not status_error:
            vmxml.sync()
            vm_xml_cxt = virsh.dumpxml(vm_name).stdout_text.strip()
            logging.debug("The VM XML with cpu cache: \n%s", vm_xml_cxt)

            # Check if vm could start successfully
            try:
                result = virsh.start(vm_name, debug=True)
                libvirt.check_exit_status(result)
            except virt_vm.VMStartError as details:
                test.fail('VM failed to start:\n%s' % details)

            # Check qemu command line and other checkpoints
            for qemuline in qemu_lines:
                libvirt.check_qemu_cmd_line(qemuline)

            if cmd_in_vm:
                vm_session = vm.wait_for_login()
                status, output = vm_session.cmd_status_output(cmd_in_vm)
                if status != 0 or not output:
                    test.fail("Failed to run command '%s'.status: [%s] output: "
                              "[%s]" % (cmd_in_vm, status, output))
                if cmd_output_regex:
                    logging.debug("checking regex %s", cmd_output_regex)
                    res = re.findall(cmd_output_regex, output.strip())
                    if string_in_cmd_output != bool(len(res)):
                        test.fail("The string '{}' {} included in {}"
                                  .format(cmd_output_regex,
                                          "is not" if string_in_cmd_output else "is",
                                          output.strip()))
        else:
            result_need_check = virsh.define(vmxml.xml, debug=True)
            libvirt.check_result(result_need_check, err_msg)

    finally:
        logging.debug("Recover test environment")
        vm.destroy(gracefully=False)
        bkxml.sync()
