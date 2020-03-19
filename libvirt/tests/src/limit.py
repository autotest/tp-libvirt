import logging
from string import ascii_letters as letters
import os

from avocado.utils import process
from avocado.utils import memory

from virttest import utils_misc
from virttest import utils_net
from virttest import virt_vm
from virttest import data_dir
from virttest import libvirt_xml
from virttest.utils_test import libvirt
from virttest.libvirt_xml.devices.interface import Interface
from virttest.libvirt_xml.devices.disk import Disk


def run(test, params, env):
    """
    cpu, memory, network, disk limit test:
    1) prepare the guest with given topology, memory and if any devices
    2) Start and login to the guest
    3) Check if the guest functional
    4) if given run some stress test

    :param test: QEMU test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    TODO: 1. Add multiple pci-bridge and pci devices respectively.
          2. Get no. VM interfaces at test start and use for validation.
    """

    failures = {"Failed to allocate KVM HPT of order":
                "Host does no have enough cma to boot, try increasing \
                kvm_cma_resv_ratio in host boot",
                "unable to map backing store for guest RAM":
                "Not enough memory in the host to boot the guest"}
    vm_name = params.get("main_vm")
    max_vcpu = current_vcpu = int(params.get("max_vcpu", 240))
    vm_cores = int(params.get("limit_vcpu_cores", 240))
    vm_threads = int(params.get("limit_vcpu_threads", 1))
    vm_sockets = int(params.get("limit_vcpu_sockets", 1))
    usermaxmem = params.get("usermaxmem", '')
    default_mem = int(params.get("default_mem", 8))
    maxmem = params.get("maxmem", "no") == "yes"
    swap_setup = params.get("swap_setup", "yes") == "yes"
    blk_partition = params.get("blk_part", '')
    graphic = params.get("graphics", "no") == "yes"
    vm = env.get_vm(vm_name)
    guestmemory = None
    max_network = params.get("max_network", "no") == "yes"
    max_disk = params.get("max_disk", "no") == "yes"
    num_network = int(params.get("num_network", 16))
    num_disk = int(params.get("num_disk", 16))
    drive_format = params.get("drive_format", "scsi")
    disk_format = params.get("disk_format", "qcow2")
    netdst = params.get("netdst", "virbr0")
    memunit = params.get("memunit", 'G')
    failed = False
    vmxml = libvirt_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    org_xml = vmxml.copy()
    # Destroy the vm
    vm.destroy()
    try:
        # Setup swap
        if swap_setup:
            if not blk_partition:
                test.cancel("block partition is not given")
            # Check if valid partition or raise
            cmd = "blkid /dev/%s|awk -F" " '{print $2}'" % blk_partition
            output = process.run(cmd, shell=True).stdout_text
            if "UUID" not in output:
                test.cancel("Not a valid partition given for swap creation")
            # Create swap partition
            cmd = "mkswap /dev/%s" % blk_partition
            process.system(cmd, shell=True)
            cmd = "swapon /dev/%s" % blk_partition
            process.system(cmd, shell=True)

        # Check for host memory and cpu levels and validate against
        # requested limits, allow to max of 10X for CPU and 2.5X for memory
        host_memory = int(memory.rounded_memtotal())
        if maxmem:
            if usermaxmem:
                guestmemory = usermaxmem
            else:
                # Normalize to GB
                guestmemory = int(2.5 * host_memory/(1024 * 1024))
        else:
            pass
        if not guestmemory:
            # assign default memory
            guestmemory = default_mem

        # Set the current and max memory params
        vmxml.current_mem_unit = memunit
        vmxml.max_mem_unit = memunit
        vmxml.current_mem = int(guestmemory)
        vmxml.max_mem = int(guestmemory)
        vmxml.sync()

        # Set vcpu and topology
        libvirt_xml.VMXML.set_vm_vcpus(vm_name, max_vcpu, current_vcpu,
                                       vm_sockets, vm_cores, vm_threads)
        vmxml = libvirt_xml.VMXML.new_from_inactive_dumpxml(vm_name)

        # Set vnc display as needed
        graphics = vmxml.get_device_class('graphics')()
        if graphic:
            if not vmxml.get_graphics_devices("vnc"):
                graphics.add_graphic(vm_name, graphic="vnc")
        else:
            if vmxml.get_graphics_devices("vnc"):
                graphics.del_graphic(vm_name)
        vmxml = libvirt_xml.VMXML.new_from_inactive_dumpxml(vm_name)

        network_str = None
        disk_str = None
        # Set network devices
        if max_network:
            network_str = "ip link|grep ^[1-9]|wc -l"
            for idx in range(num_network):
                network = Interface(type_name="bridge")
                network.mac_address = utils_net.generate_mac_address_simple()
                network.source = {"bridge": netdst}
                vmxml.add_device(network)

        # Set disk devices
        if max_disk:
            for idx in range(num_disk):
                disk_str = "lsblk|grep ^[s,v]|grep 1G|wc -l"
                disk = Disk()
                disk_path = os.path.join(data_dir.get_data_dir(), "images", "%s.qcow2" % idx)
                if "scsi" in drive_format:
                    drive_format = "scsi"
                    disk_target = "sd%s" % letters[(idx % 51)+1]
                else:
                    drive_format = "virtio"
                    disk_target = "vd%s" % letters[(idx % 51)+1]
                disk_source = libvirt.create_local_disk("file", disk_path, '1', "qcow2")
                disk.device = "disk"
                disk.source = disk.new_disk_source(**{"attrs": {'file': disk_source}})
                disk.target = {"dev": disk_target, "bus": drive_format}
                disk.driver = {"name": "qemu", 'type': disk_format}
                vmxml.add_device(disk)
        vmxml.sync()

        # Start VM
        logging.debug("VM XML: \n%s", vmxml)
        try:
            vm.start()
        except virt_vm.VMStartError as detail:
            for msg in list(failures.items()):
                if msg[0] in detail:
                    test.cancel("%s", msg[1])
            test.fail("%s" % detail)

        # Check the memory and vcpu inside guest
        memtotal = vm.get_totalmem_sys()
        cpucount = vm.get_cpu_count()
        session = vm.wait_for_login()
        if network_str:
            guestnetworks = int(session.cmd_output(network_str))
            logging.debug("guestnet: %d", guestnetworks)
            if (guestnetworks - 2) != num_network:
                failed = True
                logging.error("mismatch in guest network devices: \n"
                              "Expected: %d\nActual: %d", num_network,
                              guestnetworks)
        if disk_str:
            guestdisks = int(session.cmd_output(disk_str))
            logging.debug("guestdisk: %d", guestdisks)
            if guestdisks != num_disk:
                failed = True
                logging.error("mismatch in guest disk devices: \n"
                              "Expected: %d\nActual: %s", num_disk, guestdisks)
        session.close()
        guestmem = utils_misc.normalize_data_size("%s G" % guestmemory)
        # TODO:512 MB threshold deviation value, need to normalize
        if int(float(guestmem) - memtotal) > 512:
            failed = True
            logging.error("mismatch in guest memory: \nExpected: "
                          "%s\nActual: %s", float(guestmem), memtotal)
        if cpucount != current_vcpu:
            failed = True
            logging.error("mismatch in guest vcpu:\nExpected: %d\nActual: "
                          "%d", current_vcpu, cpucount)
        if failed:
            test.fail("Consult previous failures")
    finally:
        org_xml.sync()
