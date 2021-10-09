import logging

from provider.sriov import sriov_base

from virttest import utils_net
from virttest import utils_sriov
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices import interface
from virttest.utils_libvirt import libvirt_network
from virttest.utils_libvirt import libvirt_vfio
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    SR-IOV: managed related test.
    """
    def start_vm(vm, test_login=False, destroy_vm=False):
        """
        Start up VM

        :param vm: The vm object
        :param test_login: Whether to login VM
        :param destroy_vm: Whether to destroy VM
        """
        if vm.is_alive():
            vm.destroy()
        vm.start()
        if test_login:
            vm.wait_for_serial_login(timeout=180).close()
        if destroy_vm:
            vm.destroy()

    def create_vf_pool():
        """
        Create VF pool
        """
        net_hostdev_dict = {"net_name": params.get("net_name"),
                            "net_forward": params.get("net_forward"),
                            "vf_list_attrs": "[%s]" % utils_sriov.pci_to_addr(vf_pci)}
        libvirt_network.create_or_del_network(net_hostdev_dict)

    def check_vm_iface_managed(vm_name, iface_dict):
        """
        Check 'managed' in VM's iface

        :param vm_name: Name of VM
        :param iface_dict: The parameters dict
        :raise: TestFail if not match
        """
        vm_iface_managed = [iface.get("managed") for iface in vm_xml.VMXML.
                            new_from_dumpxml(vm_name).
                            devices.by_device_tag("interface")][0]
        expr_managed = "yes" if iface_dict.get("managed", "") == "yes" else None
        if vm_iface_managed != expr_managed:
            test.fail("Unable to get the expected managed! Actual: %s, "
                      "Expected: %s." % (vm_iface_managed, expr_managed))

    def test_networks():
        """
        Start vm with VF from VF Pool with "managed=no" or default setting

        1) Create VF pool
        2) Prepare device xml and hot-plug to the guest
        3) Detach the device from host
        4) Check the driver of device
        5) Start VM
        6) Destroy vm then check the driver
        7) Reattach the device to the host and check the driver
        """
        create_vf_pool()
        libvirt_vfio.check_vfio_pci(vf_pci, status_error=True)
        iface_dict = {"type": "network",
                      "source": "{'network': '%s'}" % params.get("net_name")}
        libvirt.modify_vm_iface(vm.name, "update_iface", iface_dict)
        res = virsh.start(vm.name, debug=True)
        libvirt.check_exit_status(res, True)

        virsh.nodedev_detach(dev_name, debug=True, ignore_status=False)
        libvirt_vfio.check_vfio_pci(vf_pci)
        start_vm(vm, True, True)
        libvirt_vfio.check_vfio_pci(vf_pci)
        virsh.nodedev_reattach(dev_name, debug=True, ignore_status=False)
        libvirt_vfio.check_vfio_pci(vf_pci, status_error=True)

    def test_device_hotplug():
        """
        Hotplug/unplug VF with managed='no'

        1) Prepare a running guest
        2) Check the driver of vf on host
        3) Prepare a xml with "managed=no"and attach to guest
        4) Detach the device from host
        5) Check the driver of vf on host
        6) Attach the device to guest
        7) Check the interface of the guest
        8) Detach the device from guest and check the driver
        9) Reattach the device to the host and check the driver
        """
        libvirt_vmxml.remove_vm_devices_by_type(vm, 'interface')
        start_vm(vm)
        libvirt_vfio.check_vfio_pci(vf_pci, status_error=True)
        mac_addr = utils_net.generate_mac_address_simple()
        iface_dict = eval(params.get('iface_dict', '{"hostdev_addr": "%s"}')
                          % utils_sriov.pci_to_addr(vf_pci))
        iface = interface.Interface("hostdev")
        iface.xml = libvirt.modify_vm_iface(vm.name, "get_xml", iface_dict)
        res = virsh.attach_device(vm_name, iface.xml, debug=True)
        libvirt.check_exit_status(res, True)
        virsh.nodedev_detach(dev_name, debug=True, ignore_status=False)
        libvirt_vfio.check_vfio_pci(vf_pci)
        virsh.attach_device(vm_name, iface.xml, debug=True,
                            ignore_status=False)

        check_vm_iface_managed(vm_name, iface_dict)
        vm.wait_for_serial_login().close()
        virsh.detach_device(vm_name, iface.xml, wait_for_event=True,
                            debug=True, ignore_status=False)
        libvirt_vfio.check_vfio_pci(vf_pci)
        virsh.nodedev_reattach(dev_name, debug=True, ignore_status=False)
        libvirt_vfio.check_vfio_pci(vf_pci, status_error=True)

    test_case = params.get("test_case", "")
    run_test = eval("test_%s" % test_case)

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    pf_pci = utils_sriov.get_pf_pci()
    if not pf_pci:
        test.cancel("NO available pf found.")
    default_vf = sriov_base.setup_vf(pf_pci, params)

    vf_pci = utils_sriov.get_vf_pci_id(pf_pci)
    dev_name = utils_sriov.get_device_name(vf_pci)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    orig_config_xml = vmxml.copy()

    try:
        run_test()

    finally:
        logging.info("Recover test environment.")
        sriov_base.recover_vf(pf_pci, params, default_vf)
        if vm.is_alive():
            vm.destroy(gracefully=False)
        orig_config_xml.sync()
        libvirt_network.create_or_del_network(
            {"net_name": params.get("net_name")}, True)
        virsh.nodedev_reattach(dev_name, debug=True)
