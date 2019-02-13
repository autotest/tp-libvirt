import logging

from avocado.utils import cpu

from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Test whether kvm guest can start after offlining part of unrelated host cpus

    1. online 4 host cpus
    2. config a vm with cpuset='0-1'
    3. shutdown vm
    4. offline unrelated host cpu '2-3'
    5. start the vm

    Expected results:
    vm start successfully after offlining part of unrelated host cpus
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    cpuset = params.get("cpuset", "0-1")
    hostcpus = params.get("hostcpus", "1,2,3,4")
    unrelated_hostcpus = params.get("unrelated_hostcpus", "2,3")

    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    vmxml_backup = vmxml.copy()

    try:
        # set vm vcpupin
        vmxml.cpuset = cpuset
        logging.debug(vmxml)
        vmxml.sync()

        # online 4 host cpus
        for x in hostcpus.split(','):
            if cpu.online(x):
                test.cancel("fail to online cpu{}".format(x))

        # start vm
        logging.info("start vm with cpuset {}".format(cpuset))
        ret = virsh.start(vm_name, debug=True)
        libvirt.check_exit_status(ret)
        vm.wait_for_login()

        # shutdown vm
        logging.info("shutdown vm")
        ret = virsh.destroy(vm_name, debug=True)
        libvirt.check_exit_status(ret)

        # offline host cpus
        logging.debug("active host cpus {}".format(cpu.cpu_online_list()))
        logging.debug("offline host cpus {}".format(unrelated_hostcpus))
        for x in unrelated_hostcpus.split(','):
            if cpu.offline(x):
                test.fail("fail to offline cpu{}".format(x))

        # check whether vm can start successfully
        logging.info("start vm")
        result = virsh.start(vm_name, debug=True)
        libvirt.check_exit_status(result)
        vm.wait_for_login()

    finally:
        vmxml_backup.sync()
