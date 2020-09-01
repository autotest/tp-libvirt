import logging
import collections

from virttest import virsh
from virttest import cpu
from virttest import libvirt_version
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
        4. check the vcpu order when hot plug/unplug specific vcpu
    negative test:
        1. run virsh setvcpu with more than one vcpu on active vm and check error
        2. run virsh setvcpu to hotplug/unplug invalid vcpu and check error
        3. enable/disable vcpu0 when vm is active/inactive and check error
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vcpu_placement = params.get("vcpu_placement", "static")
    maxvcpu = int(params.get("maxvcpu", "8"))
    vcpu_current = params.get("vcpu_current", "1")
    vcpus_enabled = eval(params.get("vcpus_enabled", "{0}"))
    vcpus_hotplug = eval(params.get("vcpus_hotpluggable", "{0}"))
    setvcpu_option = eval(params.get("setvcpu_option", "{}"))
    setvcpu_action = params.get("setvcpu_action", "")
    start_timeout = int(params.get("start_timeout", "60"))
    modify_non_hp_ol_vcpus = params.get("modify_non_hotpluggable_online", "no")
    check = params.get("check", "")
    err_msg = params.get("err_msg", "")
    status_error = "yes" == params.get("status_error", "no")

    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    vmxml_backup = vmxml.copy()

    if (modify_non_hp_ol_vcpus == "yes" and
       not libvirt_version.version_compare(6, 2, 0)):
        test.cancel("This Libvirt version doesn't initialize 'firstcpu' "
                    "variable properly.")

    def check_vcpu_status(cpulist, cpu_option, vcpus_online_pre=1):
        """
        test fail if the vcpu status from xml or the number of online vcpu from vm
        is not expected

        :param cpulist: a vcpu list set by setvcpu
        :param cpu_option: a string used by setvcpu
        :param cpus_online_pre: number of online vcpu before running setvcpu
        """
        if check.endswith("config"):
            vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        else:
            vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        logging.debug(vmxml)

        # check the vcpu status in xml
        cpu_count = 0
        for cpu_id in cpulist:
            if "enable" in cpu_option:
                cpu_count += 1
                if (vmxml.vcpus.vcpu[cpu_id].get('enabled') != "yes"):
                    test.fail("vcpu status check fail")
            elif "disable" in cpu_option:
                cpu_count -= 1
                if (vmxml.vcpus.vcpu[cpu_id].get('enabled') != "no"):
                    test.fail("vcpu status check fail")
            else:
                test.fail("wrong vcpu status in xml")

        # login vm and check the number of online vcpu
        if check == "hotplug":
            if not cpu.check_if_vm_vcpu_match(cpu_count + cpus_online_pre, vm):
                test.fail("vcpu status check fail")

    def get_vcpu_order(vmxml):
        """
        return a {vcpu:order} dict based on vcpus in xml

        :param vmxml: the instance of VMXML class
        """
        vcpu_order = {}
        # get vcpu order based on the online vcpu
        for cpu_id in range(maxvcpu):
            if vmxml.vcpus.vcpu[cpu_id].get('enabled') == "yes":
                vcpu_order[cpu_id] = int(vmxml.vcpus.vcpu[cpu_id].get('order'))

        logging.debug("vcpu order based on vcpus in xml {}".format(vcpu_order))
        return vcpu_order.copy()

    def check_vcpu_order(cpulist, cpu_option, vmxml_pre):
        """
        check the value of vcpu order in xml. when the online vcpu changes,
        the order should be redefined.

        :param cpulist: a vcpu list set by setvcpu
        :param cpu_option: a string used by setvcpu such as config, enable and live
        :param vmxml_pre: the instance of VMXML class before run setvcpu
        """
        # only one vcpu is valid in the live operation of setvcpu command
        if len(cpulist) == 1:
            vcpu = cpulist[0]
        else:
            test.fail("wrong vcpu value from cfg file")

        vmxml_new = vm_xml.VMXML.new_from_dumpxml(vm_name)
        # get vcpus order dict from previous xml
        order_pre = get_vcpu_order(vmxml_pre)
        # get vcpus order dict from updated xml
        order_new = get_vcpu_order(vmxml_new)

        # calculate the right dict of vcpu order based on the previous one
        if "enable" in cpu_option:
            order_expect = order_pre.copy()
            order_expect[vcpu] = len(order_pre) + 1
        elif "disable" in cpu_option:
            for vcpuid, order in order_pre.items():
                if order > order_pre[vcpu]:
                    order_pre[vcpuid] = order - 1
            order_pre.pop(vcpu)
            order_expect = order_pre.copy()

        if order_expect != order_new:
            test.fail("vcpu order check fail")

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

        # Remove cpu topology to avoid that it doesn't match vcpu count
        if vmxml.get_cpu_topology():
            new_cpu = vmxml.cpu
            del new_cpu.topology
            vmxml.cpu = new_cpu

        vmxml.sync()
        logging.debug(vmxml)

        # assemble setvcpu_option
        if isinstance(setvcpu_option, str):
            setvcpu_option = {setvcpu_option: setvcpu_action}

        # run virsh setvcpu and check vcpus in xml
        if check == "coldplug":
            for cpus, option in setvcpu_option.items():
                result_to_check = virsh.setvcpu(vm_name, cpus, option, debug=True)
                if not status_error:
                    cpulist = cpu.cpus_parser(cpus)
                    check_vcpu_status(cpulist, option)

        # start vm
        if check.startswith("hotplug"):
            virsh.start(vm_name, debug=True, ignore_status=False)
            vm.wait_for_login(timeout=start_timeout)

        # turn setvcpu_option to an ordered dict
        if isinstance(setvcpu_option, tuple):
            d = collections.OrderedDict()
            length = len(setvcpu_option)
            if (length % 2):
                test.fail("test config fail")
            for i in range(length):
                if not (i % 2):
                    d[setvcpu_option[i]] = setvcpu_option[i+1]
            setvcpu_option = collections.OrderedDict()
            setvcpu_option = d.copy()

        if check.startswith("hotplug"):
            for cpus, option in setvcpu_option.items():
                vmxml_pre = vm_xml.VMXML.new_from_dumpxml(vm_name)
                cpus_online_pre = vm.get_cpu_count()
                result_to_check = virsh.setvcpu(vm_name, cpus, option, debug=True)
                if not status_error:
                    cpulist = cpu.cpus_parser(cpus)
                    check_vcpu_status(cpulist, option, cpus_online_pre)
                    # check vcpu order only when live status of vcpu is changed
                    if 'config' not in option:
                        check_vcpu_order(cpulist, option, vmxml_pre)

        if 'result_to_check' in locals():
            if err_msg:
                err_msg = err_msg.split(";")
            libvirt.check_result(result_to_check, err_msg)

    finally:
        vmxml_backup.sync()
