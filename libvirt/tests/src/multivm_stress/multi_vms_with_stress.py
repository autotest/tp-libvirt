import logging as log

from avocado.utils import cpu

from virttest import utils_stress
from virttest import error_context
from virttest import utils_test
from virttest import virt_vm
from virttest import cpu as cpuutil
from virttest.libvirt_xml import vm_xml

LOG = log.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    """
    main_vm_name = params.get("main_vm")
    main_vm = env.get_vm(main_vm_name)
    # vms = env.get_all_vms()
    stress_args = params.get("stress_args", "--cpu 4 --io 4 --vm 2 --vm-bytes 128M &")

    LOG.debug("main vm name: %s", main_vm_name)

    main_vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(main_vm_name)
    main_vmxml_backup = vmxml.copy()

    vm_names = params.get("vm_names")
    vms = []



# virt-install --connect qemu:///system -n avocado-vt-vm1 --hvm --accelerate -r 2048 --vcpus=2 
# --os-variant rhel9.5 --import --noreboot --noautoconsole --serial pty --debug  
# --disk path=/var/lib/avocado/data/avocado-vt/images/jeos-27-aarch64.qcow2,bus=virtio,format=qcow2,cache=none,driver.discard=unmap,driver.io=native 
# --network network=default,model=virtio,driver.queues=2  --memballoon model=virtio --graphics vnc --boot uefi

    for vm_name in vm_names:
        disk_path = "path=/var/lib/avocado/data/avocado-vt/images/jeos-27-aarch64.qcow2,bus=virtio,format=qcow2,cache=none,driver.discard=unmap,driver.io=native"
        cmd = ("virt-install --name %s"
                " --disk %s"
                " --connect qemu:///system"
                " --hvm --accelerate"
                " -r 2048"
                " --import"
                " --noreboot --noautoconsole --nographics"
                " --serial pty"
                " --network network=default,model=virtio,driver.queues=2"
                " --memballoon model=virtio"
                "--boot uefi"
                " --debug" %
                (vm_name, disk_path))

        cmd_status_output(cmd, shell=True, timeout=600)
        LOG.debug("Installation finished for %s", vm_name)
        env.create_vm(vm_type='libvirt', target=None, name=vm_name, params=params, bindir=test.bindir)
        vms.append(env.get_vm(vm_name))
    
    env_vms = env.get_all_vms()
    LOG.debug("vms %s and %s", (vms, env_vms))

    try:
        # Get host online cpu number
        host_online_cpus = cpu.online_count()
        LOG.debug("Host online CPU number: %s", str(host_online_cpus))

        # Prepare 3 vms and each vm has even vcpus number which is about 2/3 of # host_online_cpu 
        for i, vm in enumerate(vms):
            if vm.is_alive():
                vm.destroy()
            num_vms = int(len(vms))
            vcpus_num = host_online_cpus * 2 // num_vms
            if (vcpus_num % 2 != 0):
                vcpus_num += 1
            vmxmls[i].set_vm_vcpus(vm.name, vcpus_num, vcpus_num, topology_correction=True)
            LOG.debug("vmxml for %s: %s", (vm.name, vmxmls[i]))

        # Start stress workload on the host
        # utils_test.load_stress("stress_on_host", params=params)


        # Start all vms and verify vms could be logged in normally



        # Verify all vms could be gracefully shutdown successfully



    finally:
        LOG.info("in finally")
        # # Recover VM
        # for i, vm in enumerate(vms):
        #     if vm.is_alive():
        #         vm.destroy(gracefully=False)
        #     LOG.info("Restoring vm %s...", vm.name)
        #     vmxml_backup[i].sync()