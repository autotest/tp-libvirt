import logging

from avocado.utils import cpu
from avocado.utils import process

from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest import utils_hotplug


def run(test, params, env):
    """
    Test vcpupin feature as follows:
    positive test:
        1. use vcpu cpuset to define vcpupin
        2. use cputune cpuset to define vcpupin
        3. use offline-to-online host cpu as cputune cpuset to define vcpupin
    negative test:
        1. use outrange cpuset as vcpu cpuset to define vcpupin
        2. use outrange cpuset as cputune cpuset to define vcpupin
        3. use invalid cpuset as cputune cpuset to define vcpupin
        4. use duplicate vcpu to define vcpupin
        5. use offline host cpu as cputune cpuset to define vcpupin
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    cpuset_mask = params.get("cpuset_mask", "")
    vcpu = params.get("vcpu", "")
    check = params.get("check", "")

    status_error = "yes" == params.get("status_error", "no")
    define_fail = "yes" == params.get("define_fail", "no")
    start_fail = "yes" == params.get("start_fail", "no")
    runtime_fail = "yes" == params.get("runtime_fail", "no")
    vm_down = "yes" == params.get("vm_down", "no")

    vcpu_cpuset = params.get("vcpu_cpuset", "")
    cputune_cpuset = params.get("cputune_cpuset", "")
    hostcpu = params.get("hostcpu", "")
    err_msg = params.get("err_msg", "")
    start_timeout = int(params.get("start_timeout", ""))
    offline_hostcpus = params.get("offline_hostcpus", "")
    machine_cpuset_path = params.get("machine_cpuset_path", "")

    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    vmxml_backup = vmxml.copy()

    try:
        hostcpu_num = int(cpu.total_cpus_count())

        # online all host cpus
        for x in range(hostcpu_num-1):
            if cpu.online(x):
                test.cancel("fail to online cpu{}".format(x))

        # use vcpu cpuset or/and cputune cpuset to define xml
        num = 1 if not status_error else 0
        del vmxml.cputune

        if check.startswith("vcpu") or check.endswith("outrange_cpuset"):
            vcpu_cpuset = "0-{},^{}".format(int(hostcpu_num)-num, cpuset_mask)
        vmxml.cpuset = vcpu_cpuset

        if check.startswith("cputune"):
            cputune = vm_xml.VMCPUTuneXML()
            if check.endswith("outrange_cpuset"):
                cputune_cpuset = vcpu_cpuset

            if check.endswith("duplicate_vcpu"):
                cputune.vcpupins = [{'vcpu': vcpu, 'cpuset': "2"}, {'vcpu': vcpu, 'cpuset': "3"}]
            else:
                cputune.vcpupins = [{'vcpu': vcpu, 'cpuset': cputune_cpuset}]
            vmxml.cputune = cputune

        logging.debug(vmxml)
        if status_error and define_fail:
            ret = virsh.define(vmxml.xml, debug=True, ignore_status=True)
        else:
            vmxml.sync()

        # start the vm
        if status_error and start_fail:
            ret = virsh.start(vm_name, debug=True, ignore_status=True)
        elif not status_error or runtime_fail:
            ret = virsh.start(vm_name, debug=True)
            vm.wait_for_login(timeout=start_timeout).close()

            if check.endswith("offline_hostcpu"):
                if vm_down:
                    vm.shutdown()

                logging.debug("offline host cpus {}".format(offline_hostcpus))
                for x in offline_hostcpus.split(','):
                    if cpu.offline(x):
                        test.fail("fail to offline cpu{}".format(x))

                if vm_down:
                    vm.start()
                    vm.wait_for_login(timeout=start_timeout).close()

                if status_error and check.startswith("cputune"):
                    ret = virsh.vcpupin(vm_name, vcpu, cputune_cpuset, ignore_status=True, debug=True)
                else:
                    # online host cpu and pin vcpu
                    if cpu.online(cputune_cpuset):
                        test.fail("fail to online cpu{}".format(cputune_cpuset))
                    ret = virsh.vcpupin(vm_name, vcpu, cputune_cpuset, debug=True)

            # check vcpu affinity
            if not status_error:
                affinity = vcpu_cpuset if check.startswith("vcpu") else cputune_cpuset
                affinity = {vcpu: affinity}
                virsh.vcpuinfo(vm_name, debug=True)
                if not utils_hotplug.check_affinity(vm, affinity):
                    test.fail("vcpu affinity check fail")

        libvirt.check_result(ret, err_msg)

    finally:
        vmxml_backup.sync()

        # recovery the host cpu env
        for x in range(hostcpu_num-1):
            cpu.online(x)
        cmd = "echo '0-{}' > {}".format(hostcpu_num-1, machine_cpuset_path)
        process.run(cmd, shell=True)
