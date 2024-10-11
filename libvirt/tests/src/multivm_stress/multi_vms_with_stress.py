import logging as log

from avocado.utils import cpu

from virttest import virsh
from virttest import utils_libguestfs
from virttest import utils_stress
from virttest import error_context
from virttest import utils_test
from virttest import virt_vm
from virttest import cpu as cpuutil
from virttest.libvirt_xml import vm_xml
from virttest.utils_misc import cmd_status_output

LOG = log.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    """
    main_vm_name = params.get("main_vm")
    main_vm = env.get_vm(main_vm_name)
    stress_args = params.get("stress_args", "--cpu 4 --io 4 --vm 2 --vm-bytes 128M &")

    LOG.debug("~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~")
    vm_names = params.get("vm_names").split()
    vms = [main_vm]
    vmxml_backups = []
    
    for i, vm_name in enumerate(vm_names):
        vms.append(main_vm.clone(vm_name))
        LOG.debug("Now the vms is: %s", [dom.name for dom in vms])
        LOG.debug(vms[i])
        LOG.debug(vms[i].state())
    

    LOG.debug("all vms: %s", vms)

    # Back up domain XMLs
    for vm in vms:
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm.name)
        vmxml_backups.append(vmxml.copy())

    # for i, name in enumerate(vm_names):
    #     LOG.debug("guest_name : %s", name)
    #     utils_libguestfs.virt_clone_cmd(main_vm_name, name,
    #                                     True, timeout=360,
    #                                     ignore_status=False)
    #     vms.append(main_vm.clone(name))
    #     LOG.debug("Now the vms is: %s", [dom.name for dom in vms])

    try:
        # Get host online cpu number
        host_online_cpus = cpu.online_count()
        LOG.debug("Host online CPU number: %s", str(host_online_cpus))

        # Prepare 3 vms and each vm has even vcpus number which is about 2/3 of # host_online_cpu 
        for i, vm in enumerate(vms):
            LOG.debug("~~~in for loop")
            if vm.is_alive():
                vm.destroy()
            LOG.debug("~~~vm name %s", vm.name)
            num_vms = int(len(vms))
            vcpus_num = host_online_cpus * 2 // num_vms
            LOG.debug("~~~num vms %s", num_vms)
            LOG.debug("~~~vcpus num %s", vcpus_num)
            if (vcpus_num % 2 != 0):
                vcpus_num += 1
            vm_xml.VMXML.new_from_inactive_dumpxml(vm.name).set_vm_vcpus(vm.name, vcpus_num, vcpus_num, topology_correction=True)
            # LOG.debug("vmxml for %s: %s", vm.name, vm.get_xml())


        # Start stress workload on the host
        # utils_test.load_stress("stress_on_host", params=params)


        # # Start all vms and verify vms could be logged in normally
        for vm in vms:
            LOG.debug("state before login %s", vm.state())
            vm.prepare_guest_agent()
            vm.wait_for_login()
            LOG.debug("state after login %s", vm.state())



        # # Verify all vms could be gracefully shutdown successfully
        for vm in vms:
            vm.wait_for_shutdown()
            LOG.debug("state after shutdown %s", vm.state())



    finally:
        LOG.info("in finally")
        # Recover VM
        for i, vm in enumerate(vms):
            if vm.is_alive():
                vm.destroy(gracefully=False)
            LOG.info("Restoring vm %s...", vm.name)
            vmxml_backups[i].sync()