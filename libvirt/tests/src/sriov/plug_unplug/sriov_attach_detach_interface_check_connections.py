from virttest import utils_libvirtd
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_network
from virttest.utils_libvirt import libvirt_pcicontr
from virttest.utils_test import libvirt

from provider.sriov import sriov_base


def run(test, params, env):
    """
    Check the number of connections on hostdev network
    """
    def run_test():
        """
        Test network connections

        1. Create a network
        2. Attach the interfaces and check network connections
        3. Check the network connections after detaching ifaces, restarting
            libvirtd and destroying the VM
        """
        vf_no = int(params.get("vf_no", "4"))
        net_name = network_dict.get("name")
        iface_type = params.get("iface_type", "hostdev")

        libvirt_pcicontr.reset_pci_num(vm_name)
        vm.start()
        vm.wait_for_serial_login(timeout=240)

        test.log.info("TEST_STEP1: Attach 4 interfaces to the guest.")
        opts = ' '.join(["network", net_name, params.get(
            'attach_extra_opts', "")])
        for i in range(vf_no):
            virsh.attach_interface(vm_name, option=opts, debug=True,
                                   ignore_status=False)
            libvirt_network.check_network_connection(net_name, i+1)

        test.log.info("TEST_STEP2: Try to attach one more interface.")
        res = virsh.attach_interface(vm_name, option=opts, debug=True)
        libvirt.check_exit_status(res, True)

        test.log.info("TEST_STEP3: Detach an interface.")
        vm_ifaces = [iface for iface in vm_xml.VMXML.new_from_dumpxml(vm_name).
                     devices.by_device_tag("interface")]
        mac_addr = vm_ifaces[0].get_mac_address()
        opts = ' '.join([iface_type, "--mac %s" % mac_addr])
        virsh.detach_interface(vm_name, option=opts, debug=True,
                               wait_for_event=True,
                               ignore_status=False)
        libvirt_network.check_network_connection(net_name, vf_no-1)

        test.log.info("TEST_STEP4: Restart libvirtd service and check the network connection.")
        utils_libvirtd.Libvirtd().restart()
        libvirt_network.check_network_connection(net_name, vf_no-1)

        test.log.info("TEST_STEP5: Destroy the VM and check the network connection.")
        vm.destroy(gracefully=False)
        libvirt_network.check_network_connection(net_name, 0)

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    sriov_test_obj = sriov_base.SRIOVTest(vm, test, params)
    network_dict = sriov_test_obj.parse_network_dict()

    try:
        sriov_test_obj.setup_default(network_dict=network_dict)
        run_test()

    finally:
        sriov_test_obj.teardown_default(network_dict=network_dict)
