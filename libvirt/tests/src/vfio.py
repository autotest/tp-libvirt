import logging
import tempfile
from autotest.client.shared import error
from virttest import libvirt_vm, virsh, data_dir
from virttest import remote, utils_misc, virt_vm
from virttest.utils_test import libvirt as utlv


def add_devices_to_iommu_group(vfio_controller, pci_id_list):
    """
    Add a list of pci devices to iommu group.
    """
    fail_flag = 0
    for pci_id in pci_id_list:
        if not vfio_controller.add_device_to_iommu_group(pci_id):
            fail_flag = 1
    if fail_flag:
        return False
    return True


def remove_devices_driver(pci_id_list):
    """
    Remove a list of pci devices driver
    """
    fail_flag = 0
    for pci_id in pci_id_list:
        if not utils_misc.unbind_device_driver(pci_id):
            fail_flag = 1
    if fail_flag:
        return False
    return True


def prepare_devices(pci_id, device_type):
    """
    Check whether provided pci_id is available.
    According this pci_id, get a list of devices in same iommu group.

    :param pci_id: pci device's id
    :param device_type: Ethernet or Fibre
    """
    available_type = ["Ethernet", "Fibre"]
    if device_type not in available_type:
        raise error.TestNAError("Tested device type is not supported."
                                "Available: %s" % available_type)

    # Init VFIO Controller and check whether vfio is supported.
    vfio_ctl = utils_misc.VFIOController()
    # This is devices group we classify
    devices = utils_misc.get_pci_group_by_id(pci_id, device_type)
    if len(devices) == 0:
        raise error.TestNAError("No pci device to be found.")

    # The IOMMU Group id of device we provide
    group_id = vfio_ctl.get_pci_iommu_group_id(pci_id, device_type)
    # According group id, get a group of devices system classified
    group_devices = vfio_ctl.get_iommu_group_devices(group_id)

    # Compare devices to decide whether going on
    if devices.sort() != group_devices.sort():
        raise error.TestNAError("Unknown pci device(%s) in %s groups: %s"
                                % (devices, device_type, group_devices))

    # Start to bind device driver to vfio-pci
    # Unbind from igb/lpfc
    remove_devices_driver(group_devices)
    if not add_devices_to_iommu_group(vfio_ctl, group_devices):
        raise error.TestError("Cannot add provided device to iommu group.")

    if not vfio_ctl.check_vfio_id(group_id):
        raise error.TestError("Cannot find create group id: %s" % group_id)


def cleanup_devices(pci_id, device_type):
    """
    Recover devices to original driver.
    """
    if device_type == "Ethernet":
        driver_type = "igb"
    elif device_type == "Fibre":
        driver_type = "lpfc"
    devices = utils_misc.get_pci_group_by_id(pci_id, device_type)
    remove_devices_driver(devices)
    for device in devices:
        utils_misc.bind_device_driver(device, driver_type)


def config_network(vm, interface, ip=None, mask="255.255.0.0"):
    """
    Config new added interface IP.
    If ip is None, use dhcp.
    """
    session = vm.wait_for_login()
    # Store working interface ip address
    vm_ip = vm.get_address()
    config_file = "/etc/sysconfig/network-scripts/ifcfg-%s" % interface
    mac = vm.get_interface_mac(interface)
    logging.debug("New Interface MAC:%s", mac)
    if mac is None:
        raise error.TestFail("Get new interface mac failed.")

    # Create a local file
    tmp_dir = data_dir.get_tmp_dir()
    local_file = tempfile.NamedTemporaryFile(prefix=("%s_" % interface),
                                             dir=tmp_dir)
    filepath = local_file.name
    local_file.close()
    lines = []
    lines.append("TYPE=Ethernet\n")
    lines.append("HWADDR=%s\n" % mac)
    lines.append("DEVICE=%s\n" % interface)
    if ip is not None:
        lines.append("BOOTPROTO=static\n")
        lines.append("IPADDR=%s\n" % ip)
        lines.append("NETMASK=%s\n" % mask)
    else:
        lines.append("BOOTPROTO=dhcp\n")
    lines.append("ONBOOT=no\n")
    ni = iter(lines)
    fd = open(filepath, "w")
    fd.writelines(ni)
    fd.close()

    # copy file to vm and restart interface
    remote.copy_files_to(vm_ip, "scp", "root", "123456", 22,
                         filepath, config_file)
    session.cmd("ifdown %s" % interface)
    session.cmd("ifup %s" % interface)
    logging.debug(session.cmd_output("ifconfig -a"))
    session.close()


def cleanup_vm(vm_name=None):
    """
    Cleanup the vm.
    """
    try:
        if vm_name is not None:
            virsh.undefine(vm_name)
    except error.CmdError:
        pass


def test_nic_group(vm, params):
    """
    Try to attach device in iommu group to vm.

    1.Get original available interfaces before attaching.
    2.Attaching hostdev in iommu group to vm.
    3.Start vm and check it.
    4.Check added interface in vm.
    """
    pci_id = params.get("nic_pci_id")
    device_type = "Ethernet"
    nic_ip = params.get("nic_pci_ip")
    nic_mask = params.get("nic_pci_mask", "255.255.0.0")

    # Login vm to get interfaces before attaching pci device.
    if vm.is_dead():
        vm.start()
    before_pci_nics = vm.get_pci_devices()
    before_interfaces = vm.get_interfaces()
    logging.debug("Ethernet PCI devices before:%s",
                  before_pci_nics)
    logging.debug("Ethernet interfaces before:%s",
                  before_interfaces)
    vm.destroy()

    xmlfile = utlv.create_hostdev_xml(pci_id)
    prepare_devices(pci_id, device_type)
    try:
        virsh.attach_device(domain_opt=vm.name, file_opt=xmlfile,
                            flagstr="--config", debug=True,
                            ignore_status=False)
        vm.start()
    except (error.CmdError, virt_vm.StartError), detail:
        cleanup_devices(pci_id, device_type)
        raise error.TestFail("New device does not work well: %s" % detail)

    # Get devices in vm again after attaching
    after_pci_nics = vm.get_pci_devices()
    after_interfaces = vm.get_interfaces()
    logging.debug("Ethernet PCI devices before:%s",
                  after_pci_nics)
    logging.debug("Ethernet interfaces before:%s",
                  after_interfaces)
    new_pci = "".join(list(set(before_pci_nics) ^ set(after_pci_nics)))
    new_interface = "".join(list(set(before_interfaces) ^ set(after_interfaces)))
    try:
        if not new_pci or not new_interface:
            raise error.TestFail("Cannot find attached host device in vm.")
        # Config network for new interface
        config_network(vm, new_interface, nic_ip, nic_mask)
        # Check interface
    finally:
        cleanup_devices(pci_id, device_type)


def run(test, params, env):
    """
    Test VFIO function by attaching pci device into virtual machine.

    Make sure you know that test needs to unbind default driver of
    whole iommu group to vfio-pci during test. So all the device in
    tested iommu group should be free.
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    nic_pci_id = params.get("nic_pci_id", "ETH:PCI.EXAMPLE")
    if nic_pci_id.count("EXAMPLE"):
        raise error.TestNAError("Invalid pci device id.")

    if vm.is_alive():
        vm.destroy()
    new_vm_name = "%s_vfiotest" % vm.name
    if not utlv.define_new_vm(vm.name, new_vm_name):
        raise error.TestFail("Define new vm failed.")

    try:
        new_vm = libvirt_vm.VM(new_vm_name, vm.params, vm.root_dir,
                               vm.address_cache)
        test_nic_group(new_vm, params)
    finally:
        if new_vm.is_alive():
            new_vm.destroy()
        cleanup_vm(new_vm.name)
