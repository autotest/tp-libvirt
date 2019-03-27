import logging
import ast

from virttest import virsh
from virttest import utils_misc
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Test setvcpu feature as follows:
    positive test:
        1. run virsh setvcpu with option --enable and --disable on inactive vm
           and check xml
        2. run virsh setvcpu with option --enable and --disable on active vm and
           check xml and number of online vcpu
        3. run virsh setvcpu with option --enable, --disable and --config on
           active vm and check inactive xml
    negative test:
        1. run virsh setvcpu with more than one vcpu on active vm and check error
        2. run virsh setvcpu to hotplug/unplug invalid vcpu and check error
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vcpu_placement = params.get("vcpu_placement", "static")
    maxvcpu = int(params.get("maxvcpu", "8"))
    vcpu_current = params.get("vcpu_current", "1")
    vcpus_enabled = ast.literal_eval(params.get("vcpus_enabled", "{0}"))
    vcpus_hotplug = ast.literal_eval(params.get("vcpus_hotpluggable", "{0}"))
    setvcpu_option = ast.literal_eval(params.get("setvcpu_option", "{}"))
    start_timeout = int(params.get("start_timeout", "60"))
    check = params.get("check", "")
    err_msg = params.get("err_msg", "")
    status_error = "yes" == params.get("status_error", "no")

    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    vmxml_backup = vmxml.copy()

    def check_vcpu_status(cpus_online_pre=1):
        """
        test fail if the vcpu status from xml or the number of online vcpu from vm
        is not expected

        :param cpus_online_pre: number of online vcpu before running setvcpu

        """

        if check.endswith("config"):
            vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        else:
            vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        logging.debug(vmxml)

        # check the vcpu status in xml
        cpu_count = 0
        for cpus, option in setvcpu_option.items():
            cpulist = libvirt.cpus_parser(cpus)
            for cpu_id in cpulist:
                if ("enable" in option):
                    cpu_count += 1
                    if (vmxml.vcpus.vcpu[cpu_id].get('enabled') != "yes"):
                        test.fail("vcpu status check fail")
                elif ("disable" in option):
                    cpu_count -= 1
                    if (vmxml.vcpus.vcpu[cpu_id].get('enabled') != "no"):
                        test.fail("vcpu status check fail")
                else:
                    test.fail("wrong vcpu status in xml")

        # login vm and check the number of online vcpu
        if check == "hotplug":
            if not utils_misc.check_if_vm_vcpu_match(cpu_count + cpus_online_pre, vm):
                test.fail("vcpu status check fail")

    try:
        # define vcpu in xml
        vmxml.placement = vcpu_placement
        vmxml.vcpu = maxvcpu
        vmxml.current_vcpu = vcpu_current
        del vmxml.cpuset

        # define vcpus in xml
        vcpu_list = []
        vcpu = {}

        for vcpu_id in range(maxvcpu):
            vcpu['id'] = str(vcpu_id)

            if vcpu_id in vcpus_enabled:
                vcpu['enabled'] = 'yes'
            else:
                vcpu['enabled'] = 'no'

            if vcpu_id in vcpus_hotplug:
                vcpu['hotpluggable'] = 'yes'
            else:
                vcpu['hotpluggable'] = 'no'
            vcpu_list.append(vcpu.copy())

        vcpus_xml = vm_xml.VMVCPUSXML()
        vcpus_xml.vcpu = vcpu_list
        vmxml.vcpus = vcpus_xml
        vmxml.sync()
        logging.debug(vmxml)

        # run virsh setvcpu and check vcpus in xml
        if check == "coldplug":
            for cpus, option in setvcpu_option.items():
                result_to_check = virsh.setvcpu(vm_name, cpus, option, debug=True)
                check_vcpu_status()

        # start vm
        virsh.start(vm_name, debug=True, ignore_status=False)
        vm.wait_for_login(timeout=start_timeout)

        cpus_online_pre = vm.get_cpu_count()
        if check.startswith("hotplug"):
            for cpus, option in setvcpu_option.items():
                result_to_check = virsh.setvcpu(vm_name, cpus, option, debug=True)
                if not status_error:
                    check_vcpu_status(cpus_online_pre)

        if 'result_to_check' in locals():
            if err_msg:
                err_msg = err_msg.split(";")
            libvirt.check_result(result_to_check, err_msg)

    finally:
        vmxml_backup.sync()
