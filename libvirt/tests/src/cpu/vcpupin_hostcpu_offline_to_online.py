import logging

from avocado.utils import cpu

from virttest import virsh
from virttest import utils_hotplug
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    set vcpupin when the host cpu is changed from offline to online

    1. set cpuset in xml to 0-1 and online host cpu 0-3
    2. start vm
    3. offline the host cpu 3
    4. pin the vcpu 0 to host cpu 3 and failed
    5. online the host cpu 3
    6. pin the vcpu 0 to host cpu 3 and succeed
    7. check vcpu affinity
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    cpuset = params.get("cpuset", "0-3")
    hostcpus = params.get("hostcpus", "0,1,2,3")
    hostcpu = params.get("hostcpu", "3")
    vcpu = params.get("vcpu", "0")

    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    vmxml_backup = vmxml.copy()

    try:
        vmxml.cpuset = cpuset
        del vmxml.cputune
        logging.debug(vmxml)
        vmxml.sync()

        # online 4 host cpus
        for x in hostcpus.split(','):
            if cpu.online(x):
                test.cancel("fail to online cpu{}".format(x))

        # start vm
        vm.start()
        vm.wait_for_login().close()

        # offline host cpu and pin vcpu
        if cpu.offline(hostcpu):
            test.fail("fail to offline cpu{}".format(hostcpu))
        ret = virsh.vcpupin(vm_name, vcpu, hostcpu, ignore_status=True, debug=True)
        libvirt.check_result(ret, "error: cannot set CPU affinity : Invalid argument")

        # online host cpu and pin vcpu
        if cpu.online(hostcpu):
            test.fail("fail to online cpu{}".format(hostcpu))
        ret = virsh.vcpupin(vm_name, vcpu, hostcpu, debug=True)
        libvirt.check_result(ret)

        # check vcpu affinity
        if not utils_hotplug.check_affinity(vm, {vcpu: hostcpu}):
            logging.info("vcpu affinity check fail")

    finally:
        vmxml_backup.sync()
