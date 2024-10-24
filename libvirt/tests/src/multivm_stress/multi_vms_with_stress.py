import logging as log

from avocado.utils import cpu

from virttest import utils_test
from virttest.libvirt_xml import vm_xml

LOG = log.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test that multiple vms can start when stress workload is running on the host.

    Steps:
    1. Prepare 3 vms that each have even vcpu number around 2/3 of # host_online_cpu
    2. Start stress workload on the host
    3. Start all vms and verify vms could be logged in normally
    4. Verify all vms could be gracefully shutdown successfully
    """
    memory = params.get("memory", "4194304")
    main_vm_name = params.get("main_vm")
    main_vm = env.get_vm(main_vm_name)
    vm_names = params.get("vm_names").split()
    vms = [main_vm]
    vmxml_backups = []

    # Get vms
    for i, vm_name in enumerate(vm_names):
        vms.append(main_vm.clone(vm_name))

    for vm in vms:
        # Back up domain XMLs
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm.name)
        vmxml_backups.append(vmxml.copy())
        # Increase memory
        vmxml.memory = int(memory)
        vmxml.current_mem = int(memory)
        vmxml.sync()

    try:
        # Get host online cpu number
        host_online_cpus = cpu.online_count()
        LOG.debug("Host online CPU number: %s", str(host_online_cpus))

        # Prepare 3 vms and each vm has even vcpus number which is about 2/3 of # host_online_cpu
        for i, vm in enumerate(vms):
            if vm.is_alive():
                vm.destroy()
            vcpus_num = host_online_cpus * 2 // int(len(vms))
            if (vcpus_num % 2 != 0):
                vcpus_num += 1
            vm_xml.VMXML.new_from_inactive_dumpxml(vm.name).set_vm_vcpus(vm.name, vcpus_num, vcpus_num, topology_correction=True)
            LOG.debug("Defined vm %s with '%s' vcpu(s)", vm.name, str(vcpus_num))

        # Start stress workload on the host
        # params must include stress_args
        utils_test.load_stress("stress_on_host", params=params)

        # Start all vms and verify vms could be logged in normally
        for vm in vms:
            vm.prepare_guest_agent()
            vm.wait_for_login()
            if (vm.state() != "running"):
                test.fail("VM %s should be running, not %s" % (vm.name, vm.state()))

        # Verify all vms could be gracefully shutdown successfully
        for vm in vms:
            vm.shutdown()
            if (vm.state() != "shut off"):
                test.fail("VM %s should be shut off, not %s" % (vm.name, vm.state()))

    finally:
        # Stop stress workload
        utils_test.unload_stress("stress_on_host", params=params)

        # Recover VMs
        for i, vm in enumerate(vms):
            if vm.is_alive():
                vm.destroy(gracefully=False)
            LOG.info("Restoring vm %s...", vm.name)
            vmxml_backups[i].sync()
