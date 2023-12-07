from virttest import utils_misc
from virttest import utils_sriov
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_network
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.interface import interface_base
from provider.sriov import check_points
from provider.sriov import sriov_base


def get_vm_iface_num(vm_name):
    """
    Get VM virtual interfaces' number

    :param vm_name: The name of VM
    :return: VM interfaces' number
    """
    res = virsh.domiflist(vm_name, debug=True)
    return len(res.stdout_text.strip().splitlines()[2::])


def create_network(net_name, pf_name, params):
    """
    Create network

    :param net_name: Network name to create
    :param pf_name: PF device
    :param params: The parameters dict
    """
    net_dict = {"name": net_name,
                "forward": eval(params.get("net_forward")),
                "pf": {"dev": pf_name}
                }
    libvirt_network.create_or_del_network(net_dict)


def get_pf_id_list(pf_info, driver):
    """
    Get id of PFs

    :param pf_info: Dict, pfs' info
    :param driver: str, pfs' driver
    :return: List, pfs' id, eg. ['0000:05:00.0', '0000:05:00.1']
    """
    return [pf.get("pci_id") for pf in pf_info.values()
            if pf.get("driver") == driver]


def run(test, params, env):
    """
    Test maximum hostdev interfaces on the vm
    """
    def setup_test():
        """
        Setup for max_vfs case

        1. Check test environment
        2. Enable VFs
        3. Create networks
        """
        if not utils_misc.compare_qemu_version(4, 0, 0, False):
            test.cancel("This test is supported from qemu-kvm 4.0.0.")
        if len(pf_info) < 2:
            test.cancel("This test requires at least 2 PFs.")

        pf_id_list = get_pf_id_list(pf_info, driver)
        for pf_pci in pf_id_list:
            sriov_base.recover_vf(pf_pci, params)
            sriov_base.setup_vf(pf_pci, params)

        for pf_dev, net_name in net_info.items():
            test.log.info("TEST_SETUP: Create network %s.", net_name)
            create_network(net_name, pf_dev, params)

        test.log.info("TEST_SETUP: Remove VM's interface devices.")
        libvirt_vmxml.remove_vm_devices_by_type(vm, 'interface')

        test.log.info(f"TEST_SETUP: Cold plug {iface_num} interfaces to VM.")
        opts = "network %s --config" % list(net_info.values())[0]
        for i in range(vf_no):
            iface_dict = {
                'address': {'type_name': 'pci',
                            'attrs': {'bus': '%0#4x' % int(7 + i // 8),
                                      'domain': '0x0000',
                                      'function': '%0#1x' % int(i % 8),
                                      'slot': '0x00',
                                      'type': 'pci'}},
                'source': {'network': list(net_info.values())[0]},
                'type_name': 'network'}
            if i % 8 == 0:
                iface_dict['address']['attrs'].update({'multifunction': 'on'})
            iface_dev = interface_base.create_iface("network", iface_dict)
            virsh.attach_device(vm.name, iface_dev.xml, flagstr="--config",
                                debug=True, ignore_status=False)
        for i in range(iface_num - vf_no):
            net_name_2 = list(net_info.values())[1]
            opts = "network %s --config" % net_name_2
            virsh.attach_interface(vm_name, opts, debug=True, ignore_status=False)
        compare_vm_iface(test, get_vm_iface_num(vm_name), iface_num)

    def teardown_test():
        """
        Teardown for max_vfs case

        1. Disable VFs
        2. Clean up networks
        """
        pf_id_list = get_pf_id_list(pf_info, driver)
        for pf_pci in pf_id_list:
            sriov_base.recover_vf(pf_pci, params, timeout=240)
        net_info = get_net_dict(pf_info)
        for pf_dev in net_info:
            libvirt_network.create_or_del_network(
                        {"name": net_info[pf_dev]}, True)

    def run_test():
        """
        Verify vm can work well with maximum hostdev interfaces

        1. Start vm with 64 vfio interfaces
        2. Check networks
        3. Reboot the vm and check network function again
        4. Suspend and resume, then check network function
        5. Try to hot plug the 65th hostdev interface
        """
        test.log.info("TEST_STEP1: Start the VM and check networks.")
        result = virsh.start(vm.name, debug=True)
        libvirt.check_exit_status(result, start_error)
        if start_error:
            return
        vm.create_serial_console()
        vm_session = vm.wait_for_serial_login(timeout=240)
        res = vm_session.cmd_status_output(
            'lspci |grep Ether')[1].strip().splitlines()
        compare_vm_iface(test, len(res), vf_no+1)
        check_points.check_vm_network_accessed(vm_session)

        test.log.info("TEST_STEP2: Reboot the VM and check networks.")
        virsh.reboot(vm.name, debug=True, ignore_status=False)
        vm_session = vm.wait_for_serial_login(timeout=240)
        check_points.check_vm_network_accessed(vm_session)

        test.log.info("TEST_STEP3: Suspend and resume the VM and check networks.")
        virsh.suspend(vm.name, debug=True, ignore_status=False)
        virsh.resume(vm.name, debug=True, ignore_status=False)
        check_points.check_vm_network_accessed(vm_session)

        test.log.info("TEST_STEP4: Hot Plug the 65th iface.")
        net_name_2 = list(net_info.values())[1]
        opts_hotplug = "network %s" % net_name_2
        res = virsh.attach_interface(vm_name, opts_hotplug)
        libvirt.check_exit_status(res, True)

    def get_net_dict(pf_info):
        """
        Get network dict from pfs info

        :param pf_info: PFs info
        :return: Network parameters
        """
        pf_id_list = get_pf_id_list(pf_info, driver)
        return dict(zip([utils_sriov.get_iface_name(pf_pci)
                        for pf_pci in pf_id_list], ['hostdevnet'+str(x) for x in
                                                    range(len(pf_id_list))]))

    def compare_vm_iface(test, vm_iface_num, expr_no):
        """
        Compare the number of VM interfaces with the expected number

        :param test: test object
        :param vm_iface_num: The number of vm interface
        :param expr_no: Expected number of ifaces
        """
        if expr_no != vm_iface_num:
            test.fail("The number of vm ifaces is incorrect! Expected: %d, "
                      "Actual: %d." % (expr_no, vm_iface_num))
        else:
            test.log.debug("The number of VM ifaces is %d.", vm_iface_num)

    pf_info = utils_sriov.get_pf_info()
    pf_pci = utils_sriov.get_pf_pci()
    driver = utils_sriov.get_pf_info_by_pci(pf_pci).get('driver')
    net_info = get_net_dict(pf_info)
    vf_no = int(params.get("vf_no", "63"))
    iface_num = int(params.get("iface_num", "64"))
    start_error = "yes" == params.get("start_error", "no")

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    orig_config_xml = vmxml.copy()

    try:
        setup_test()
        run_test()

    finally:
        test.log.info("TEST_TEARDOWN: Recover test enviroment.")
        if vm.is_alive():
            vm.destroy(gracefully=False)
        orig_config_xml.sync()
        teardown_test()
