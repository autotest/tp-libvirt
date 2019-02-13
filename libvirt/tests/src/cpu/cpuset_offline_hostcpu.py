import logging

from avocado.utils import cpu
from avocado.utils import process

from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest import utils_hotplug


def run(test, params, env):
    """
    1. Test whether kvm guest can start after offlining part of unrelated host cpus
    2. set vcpupin when the host cpu is changed from offline to online

    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vcpu = params.get("vcpu", "0")
    cpuset = params.get("cpuset", "0-1")
    hostcpu = params.get("hostcpu", "3")
    hostcpus = params.get("hostcpus", "0,1,2,3")
    hostcpus_offline = params.get("hostcpus_offline", "2,3")
    machine_cpuset_path = params.get("machine_cpuset_path", "")
    vm_status = params.get("vm_status", "")
    err_msg = params.get("err_msg", "")
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    vmxml_backup = vmxml.copy()

    try:
        vmxml.cpuset = cpuset
        logging.debug(vmxml)
        vmxml.sync()

        # online 4 host cpus
        for x in hostcpus.split(','):
            if cpu.online(x):
                test.cancel("fail to online cpu{}".format(x))

        # start vm
        logging.info("start vm with cpuset {}".format(cpuset))
        vm.start()
        vm.wait_for_login()

        if vm_status == "vm_down":
            vm.shutdown()

        # offline host cpus
        logging.debug("offline host cpus {}".format(hostcpus_offline))
        for x in hostcpus_offline.split(','):
            if cpu.offline(x):
                test.fail("fail to offline cpu{}".format(x))

        if vm_status == "vm_down":
            vm.start()
            vm.wait_for_login()
        elif vm_status == "vm_up":
            # offline host cpu and pin vcpu
            if cpu.offline(hostcpu):
                test.fail("fail to offline cpu{}".format(hostcpu))
            ret = virsh.vcpupin(vm_name, vcpu, hostcpu, ignore_status=True, debug=True)
            libvirt.check_result(ret, err_msg)

            # online host cpu and pin vcpu
            if cpu.online(hostcpu):
                test.fail("fail to online cpu{}".format(hostcpu))
            ret = virsh.vcpupin(vm_name, vcpu, hostcpu, debug=True)
            libvirt.check_result(ret)

            if not utils_hotplug.check_affinity(vm, {vcpu: hostcpu}):
                logging.info("vcpu affinity check fail")

    finally:
        vmxml_backup.sync()

        # recovery the host cpu env
        for x in hostcpus_offline.split(','):
            cpu.online(x)

        hostcpu_num = int(cpu.total_cpus_count())
        cmd = "echo '0-{}' > {}".format(hostcpu_num-1, machine_cpuset_path)
        process.run(cmd, shell=True)
