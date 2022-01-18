import logging as log

from provider.sriov import sriov_base

from virttest import utils_libvirtd
from virttest import utils_net
from virttest import utils_sriov
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices import interface
from virttest.utils_libvirt import libvirt_network
from virttest.utils_libvirt import libvirt_pcicontr
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def create_network(params):
    """
    Create VF pool
    """
    net_dict = {"net_name": params.get("net_name"),
                "net_forward": params.get("net_forward")}
    net_forward_pf = "yes" == params.get("net_forward_pf")
    if net_forward_pf:
        net_dict.update(
            {"net_forward_pf": '{"dev": "%s"}' % params.get("pf_name")})
    else:
        net_dict.update({"forward_iface": params.get("vf_iface")})
    libvirt_network.create_or_del_network(net_dict)


def create_iface(iface_dict):
    """
    Create Interface device

    :param iface_dict: Dict, attrs of Interface
    :return: xml object of interface
    """
    iface = interface.Interface("network")
    iface.setup_attrs(**iface_dict)

    logging.debug("Interface XML: %s", iface)
    return iface


def run(test, params, env):
    """
    Test interfaces attached from network
    """

    def test_at_dt():
        """
        Test attach-detach interfaces
        """
        if not pf_status:
            logging.info("Set pf state to down.")
            pf_iface_obj = utils_net.Interface(pf_name)
            pf_iface_obj.down()

        logging.info("Define network - %s.", params.get("net_name"))
        create_network(params)

        logging.debug("Remove VM's interface devices.")
        libvirt_vmxml.remove_vm_devices_by_type(vm, 'interface')
        vm.start()
        vm_session = vm.wait_for_serial_login(timeout=240)

        logging.info("Hotplug an interface to VM.")
        iface_dict = {"model": "virtio",
                      "source": {'network': params.get("net_name")}}
        iface = create_iface(iface_dict)
        res = virsh.attach_device(vm_name, iface.xml, debug=True)
        libvirt.check_exit_status(res, status_error)
        if not pf_status:
            logging.info("Set pf state to up then check again.")
            pf_iface_obj.up()
            virsh.attach_device(vm_name, iface.xml, debug=True,
                                ignore_status=False)
        libvirt_vmxml.check_guest_xml(vm.name, params["net_name"])
        sriov_base.check_vm_network_accessed(vm_session)

    def test_connection():
        """
        Test network connections

        1. Create a network
        2. Attach the interfaces and check network connections
        3. Check the network connections after detaching ifaces, restarting
            libvirtd and destroying the VM
        """
        vf_no = int(params.get("vf_no", "4"))
        net_name = params.get("net_name")
        iface_type = params.get("iface_type")

        logging.info("Define network - %s.", net_name)
        create_network(params)
        libvirt_vmxml.remove_vm_devices_by_type(vm, 'interface')
        libvirt_pcicontr.reset_pci_num(vm_name)
        vm.start()
        vm.wait_for_serial_login(timeout=240)

        logging.info("Attach 4 interfaces to the guest.")
        opts = ' '.join(["network", net_name, params.get(
            'attach_extra_opts', "")])
        for i in range(vf_no):
            virsh.attach_interface(vm_name, option=opts, debug=True,
                                   ignore_status=False)
            libvirt_network.check_network_connection(net_name, i+1)

        logging.info("Try to attach one more interface.")
        res = virsh.attach_interface(vm_name, option=opts, debug=True)
        libvirt.check_exit_status(res, True)

        logging.info("Detach an interface.")
        vm_ifaces = [iface for iface in vm_xml.VMXML.new_from_dumpxml(vm_name).
                     devices.by_device_tag("interface")]
        mac_addr = vm_ifaces[0].get_mac_address()
        opts = ' '.join([iface_type, "--mac %s" % mac_addr])
        virsh.detach_interface(vm_name, option=opts, debug=True,
                               wait_for_event=True,
                               ignore_status=False)
        libvirt_network.check_network_connection(net_name, vf_no-1)

        logging.info("Restart libvirtd service and check the network connection.")
        utils_libvirtd.Libvirtd().restart()
        libvirt_network.check_network_connection(net_name, vf_no-1)
        logging.info("Destroy the VM and check the network connection.")
        vm.destroy(gracefully=False)
        libvirt_network.check_network_connection(net_name, 0)

    test_case = params.get("test_case", "")
    run_test = eval("test_%s" % test_case)
    status_error = "yes" == params.get("status_error", "no")

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    pf_pci = utils_sriov.get_pf_pci()
    if not pf_pci:
        test.cancel("NO available pf found.")
    sriov_base.setup_vf(pf_pci, params)

    vf_pci = utils_sriov.get_vf_pci_id(pf_pci)
    params['vf_iface'] = utils_sriov.get_iface_name(vf_pci)
    pf_status = "active" == params.get("pf_status", "active")
    pf_name = utils_sriov.get_pf_info_by_pci(pf_pci).get('iface')
    params['pf_name'] = pf_name
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    orig_config_xml = vmxml.copy()

    try:
        run_test()

    finally:
        logging.info("Recover test enviroment.")
        if not pf_status:
            pf_iface_obj = utils_net.Interface(pf_name)
            pf_iface_obj.up()
        sriov_base.recover_vf(pf_pci, params)
        if vm.is_alive():
            vm.destroy(gracefully=False)
        orig_config_xml.sync()
        libvirt_network.create_or_del_network(
            {"net_name": params.get("net_name")}, True)
