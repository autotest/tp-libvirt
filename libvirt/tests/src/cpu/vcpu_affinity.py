import logging

from avocado.utils import cpu
from avocado.utils import process

from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml import xcepts
from virttest.utils_test import libvirt
from virttest import utils_hotplug


def check_vcpu_placement(test, params):
    """
    test fail if the value of placement or cpuset from xml is not expected

    :param test: instance of avocado test class
    :param params: instance of avocado params class
    """
    vm_name = params.get("main_vm")
    numatune_memory_mode = params.get("numatune_memory_mode", "")
    numatune_memory_placement = params.get("numatune_memory_placement", "")

    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    logging.debug(vmxml)

    flag = True
    if (vmxml.placement == "static" and vmxml.cpuset == "1"):
        flag = False

    if (vmxml.placement == "auto" and hasattr(vmxml, "numa_memory")):
        if (vmxml.numa_memory.get('mode') == numatune_memory_mode and
           vmxml.numa_memory.get('placement') == numatune_memory_placement):
            flag = False

    if flag:
        test.fail("vcpu placement check fail")


def run(test, params, env):
    """
    Test vcpu affinity feature as follows:
    positive test:
        1. use vcpu cpuset in xml to define vcpu affinity
        2. use cputune cpuset in xml to define vcpu affinity
        3. use offline-to-online host cpu as cpuset to run virsh vcpupin
        4. set vcpu placement in xml to auto and check xml result
        5. set vcpu cpuset in xml without placement defined and check xml result
    negative test:
        1. use outrange cpuset as vcpu cpuset in xml to define vcpu affinity
        2. use outrange cpuset as cputune cpuset in xml to define vcpu affinity
        3. use invalid cpuset as cputune cpuset in xml to define vcpu affinity
        4. use duplicate vcpu in xml to define vcpu affinity
        5. use offline host cpu as cputune cpuset to run virsh vcpupin
        6. set vcpu affinity for none exists vcpu and check xml result
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    cpuset_mask = params.get("cpuset_mask", "")
    vcpu = params.get("vcpu", "0")
    maxvcpu = params.get("maxvcpu", "8")
    current_vcpu = params.get("current_vcpu", "3")
    check = params.get("check", "")
    config_xml = params.get("config_xml", "")

    status_error = "yes" == params.get("status_error", "no")
    define_fail = "yes" == params.get("define_fail", "no")
    start_fail = "yes" == params.get("start_fail", "no")
    runtime_fail = "yes" == params.get("runtime_fail", "no")
    vm_down = "yes" == params.get("vm_down", "no")

    vcpu_cpuset = params.get("vcpu_cpuset", "")
    cputune_cpuset = params.get("cputune_cpuset", "")
    vcpu_placement = params.get("vcpu_placement", "static")
    err_msg = params.get("err_msg", "")
    start_timeout = int(params.get("start_timeout", "60"))
    offline_hostcpus = params.get("offline_hostcpus", "")
    machine_cpuset_path = params.get("machine_cpuset_path", "")

    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    vmxml_backup = vmxml.copy()

    def check_vcpu_affinity():
        """
        check vcpu affinity defined by vcpu cpuset or cputune cpuset
        """
        affinity = vcpu_cpuset if not cputune_cpuset else cputune_cpuset
        affinity = {vcpu: affinity}
        virsh.vcpuinfo(vm_name, debug=True)

        vmxml_live = vm_xml.VMXML.new_from_dumpxml(vm_name)
        logging.debug(vmxml_live)

        # if vcpu >= maxvcpu, the cputune should not exist in xml
        if int(vcpu) >= int(maxvcpu):
            try:
                if hasattr(vmxml_live, 'cputune'):
                    test.fail("cputune tag is set when vcpu >= maxvcpu")
        # check the expected vcpu affinity with the one got from running vm
                elif not utils_hotplug.check_affinity(vm, affinity):
                    test.fail("vcpu affinity check fail")
            except xcepts.LibvirtXMLError:
                pass

    try:
        hostcpu_num = int(cpu.total_cpus_count())

        # online all host cpus
        for x in range(hostcpu_num):
            if cpu.online(x):
                test.fail("fail to online cpu{}".format(x))

        # use vcpu cpuset or/and cputune cpuset to define xml
        del vmxml.cputune
        del vmxml.vcpus
        del vmxml.placement
        vmxml.vcpu = int(maxvcpu)
        vmxml.current_vcpu = current_vcpu

        # config vcpu cpuset for cpuset range test
        num = 1 if not status_error else 0
        cpuset_new = "0-{},^{}".format(hostcpu_num-num, cpuset_mask)
        if (config_xml == "vcpu" and check.endswith("range_cpuset")):
            vcpu_cpuset = cpuset_new
        vmxml.cpuset = vcpu_cpuset

        if vcpu_placement:
            vmxml.placement = vcpu_placement

        if config_xml == "cputune":
            cputune = vm_xml.VMCPUTuneXML()
            if check.endswith("range_cpuset"):
                cputune_cpuset = cpuset_new
            if check.endswith("duplicate_vcpu"):
                cputune.vcpupins = [{'vcpu': vcpu, 'cpuset': "2"}, {'vcpu': vcpu, 'cpuset': "3"}]
            else:
                cputune.vcpupins = [{'vcpu': vcpu, 'cpuset': cputune_cpuset}]
            vmxml.cputune = cputune

        logging.debug(vmxml)
        if status_error and define_fail:
            result_to_check = virsh.define(vmxml.xml, debug=True)
        else:
            vmxml.sync()

        # start the vm
        if status_error and start_fail:
            result_to_check = virsh.start(vm_name, debug=True)

        if (not status_error) or runtime_fail:
            result_to_check = virsh.start(vm_name, debug=True, ignore_status=False)
            vm.wait_for_login(timeout=start_timeout).close()

            # test vcpu cpuset in offline/online  host cpu scenario
            if check.endswith("offline_hostcpu"):
                if vm_down:
                    vm.shutdown()
                for x in offline_hostcpus.split(','):
                    if cpu.offline(x):
                        test.fail("fail to offline cpu{}".format(x))
                    logging.debug("offline host cpu {}".format(x))
                if vm_down:
                    vm.start()
                    vm.wait_for_login(timeout=start_timeout).close()
                if not status_error:
                    # online host cpu
                    if cpu.online(cputune_cpuset):
                        test.fail("fail to online cpu{}".format(cputune_cpuset))

            # run virsh vcpupin to config vcpu affinity
            if check.startswith("cputune") and (not config_xml):
                result_to_check = virsh.vcpupin(vm_name, vcpu, cputune_cpuset, debug=True)

            if check == "vcpu_placement":
                check_vcpu_placement(test, params)
            elif not status_error:
                check_vcpu_affinity()

        if 'result_to_check' in locals():
            if err_msg:
                err_msg = err_msg.split(";")
            libvirt.check_result(result_to_check, err_msg)

    finally:
        vmxml_backup.sync()

        # recovery the host cpu env
        for x in range(hostcpu_num):
            cpu.online(x)
        cmd = "echo '0-{}' > {}".format(hostcpu_num-1, machine_cpuset_path)
        process.run(cmd, shell=True)
