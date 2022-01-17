import logging as log

from provider.sriov import sriov_base

from virttest import utils_misc
from virttest import utils_sriov
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_network
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


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
    net_dict = {"net_name": net_name,
                "net_forward": params.get("net_forward"),
                "net_forward_pf": '{"dev": "%s"}' % pf_name
                }
    libvirt_network.create_or_del_network(net_dict)


def get_pf_id_list(pf_info):
    """
    Get id of PFs

    :param pf_info: Dict, pfs' info
    :return: List, pfs' id, eg. ['0000:05:00.0', '0000:05:00.1']
    """
    return [pf.get("pci_id") for pf in pf_info.values()]


def run(test, params, env):
    """
    Test interfaces attached from network
    """
    def setup_default():
        """
        Default setup
        """
        pass

    def teardown_default():
        """
        Default cleanup
        """
        pass

    def setup_max_vfs():
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

        pf_id_list = get_pf_id_list(pf_info)
        for pf_pci in pf_id_list:
            sriov_base.recover_vf(pf_pci, params)
            sriov_base.setup_vf(pf_pci, params)

        net_info = get_net_dict(pf_info)
        for pf_dev, net_name in net_info.items():
            create_network(net_name, pf_dev, params)

    def teardown_max_vfs():
        """
        Teardown for max_vfs case

        1. Disable VFs
        2. Clean up networks
        """
        pf_id_list = get_pf_id_list(pf_info)
        for pf_pci in pf_id_list:
            sriov_base.recover_vf(pf_pci, params, timeout=240)
        net_info = get_net_dict(pf_info)
        for pf_dev in net_info:
            libvirt_network.create_or_del_network(
                        {"net_name": net_info[pf_dev]}, True)

    def test_max_vfs():
        """
        Hotplug MAX VFs to guest

        1. Start vm with 64 vfio interfaces
        2. Check networks
        3. Try to hot plug the 65th hostdev interface
        4. Destroy vm and cold plug 1 hostdev interface
        """
        net_info = get_net_dict(pf_info)
        vf_no = int(params.get("vf_no", "63"))

        logging.debug("Remove VM's interface devices.")
        libvirt_vmxml.remove_vm_devices_by_type(vm, 'interface')
        compare_vm_iface(test, get_vm_iface_num(vm_name), 0)

        logging.info("Cold plug 64 interfaces to VM.")
        opts = "network %s --config" % list(net_info.values())[0]
        for i in range(vf_no):
            virsh.attach_interface(vm_name, opts, debug=True, ignore_status=False)
        net_name_2 = list(net_info.values())[1]
        opts = "network %s --config" % net_name_2
        virsh.attach_interface(vm_name, opts, debug=True, ignore_status=False)
        compare_vm_iface(test, get_vm_iface_num(vm_name), vf_no+1)

        logging.info("Start VM and check networks.")
        vm.start()
        vm_session = vm.wait_for_serial_login(timeout=240)
        res = vm_session.cmd_status_output(
            'lspci |grep Ether')[1].strip().splitlines()
        compare_vm_iface(test, len(res), vf_no+1)

        logging.info("Hot Plug the 65th iface.")
        opts_hotplug = "network %s" % net_name_2
        res = virsh.attach_interface(vm_name, opts_hotplug)
        libvirt.check_exit_status(res, True)

        logging.info("Destroy vm and cold plug the 65th hostdev interface.")
        vm.destroy()
        virsh.attach_interface(vm_name, opts, ignore_status=False)

        compare_vm_iface(test, get_vm_iface_num(vm_name), vf_no+2)
        res = virsh.start(vm_name, debug=True)
        libvirt.check_exit_status(res, True)

    def get_net_dict(pf_info):
        """
        Get network dict from pfs info

        :param pf_info: PFs info
        :return: Network parameters
        """
        pf_id_list = get_pf_id_list(pf_info)
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
            logging.debug("The number of VM ifaces is %d.", vm_iface_num)

    test_case = params.get("test_case", "")
    run_test = eval("test_%s" % test_case)
    setup_test = eval("setup_%s" % test_case) if "setup_%s" % test_case in \
        locals() else setup_default
    teardown_test = eval("teardown_%s" % test_case) if "teardown_%s" % \
        test_case in locals() else teardown_default

    pf_info = utils_sriov.get_pf_info()

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    orig_config_xml = vmxml.copy()

    try:
        setup_test()
        run_test()

    finally:
        logging.info("Recover test enviroment.")
        if vm.is_alive():
            vm.destroy(gracefully=False)
        orig_config_xml.sync()
        teardown_test()
