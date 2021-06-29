import logging
import tempfile
import re

from avocado.utils import process

from virttest import libvirt_vm
from virttest import virsh
from virttest import data_dir
from virttest import remote
from virttest import utils_misc
from virttest import virt_vm
from virttest.utils_test import libvirt as utlv
from virttest.libvirt_xml import vm_xml


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


def prepare_devices(test, pci_id, device_type, only=False):
    """
    Check whether provided pci_id is available.
    According this pci_id, get a list of devices in same iommu group.

    :param pci_id: pci device's id
    :param device_type: Ethernet or Fibre
    :param only: only the device provided will be binded.
    """
    available_type = ["Ethernet", "Fibre"]
    if device_type not in available_type:
        test.cancel("Tested device type is not supported."
                    "Available: %s" % available_type)

    # Init VFIO Controller and check whether vfio is supported.
    vfio_ctl = utils_misc.VFIOController()
    # This is devices group we classify
    devices = utils_misc.get_pci_group_by_id(pci_id, device_type)
    if len(devices) == 0:
        test.cancel("No pci device to be found.")

    # The IOMMU Group id of device we provide
    group_id = vfio_ctl.get_pci_iommu_group_id(pci_id, device_type)
    # According group id, get a group of devices system classified
    group_devices = vfio_ctl.get_iommu_group_devices(group_id)
    if only:
        # Make sure other devices is not vfio-pci driver
        cleanup_devices(pci_id, device_type)
        for device in group_devices:
            if device.count(pci_id):
                pci_id = device
                break
        group_devices = [pci_id]

    # Compare devices to decide whether going on
    if devices.sort() != group_devices.sort():
        test.cancel("Unknown pci device(%s) in %s groups: %s"
                    % (devices, device_type, group_devices))

    # Start to bind device driver to vfio-pci
    # Unbind from igb/lpfc
    if not add_devices_to_iommu_group(vfio_ctl, group_devices):
        test.error("Cannot add provided device to iommu group.")

    if not vfio_ctl.check_vfio_id(group_id):
        test.error("Cannot find create group id: %s" % group_id)


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


def get_windows_disks(vm):
    """Get disks in windows"""
    session = vm.wait_for_login()
    output = session.cmd_output("wmic diskdrive").strip()
    logging.debug(output)
    session.close()
    disks = []
    for line in output.splitlines()[1:]:
        diskID = re.search("PHYSICALDRIVE\d", line)
        if diskID is not None:
            disks.append(diskID.group(0))
    return disks


def config_network(test, vm, interface, ip=None, mask="255.255.0.0", gateway=None):
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
        test.fail("Get new interface mac failed.")

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
        if gateway is not None:
            lines.append("GATEWAY=%s\n" % gateway)
    else:
        lines.append("BOOTPROTO=dhcp\n")
    lines.append("ONBOOT=no\n")
    ni = iter(lines)
    with open(filepath, "w") as fd:
        fd.writelines(ni)

    # copy file to vm and restart interface
    remote.copy_files_to(vm_ip, "scp", "root", "123456", 22,
                         filepath, config_file)
    try:
        session.cmd("ifdown %s" % interface)
    except Exception:
        pass    # The device may be not active, try to up it anyway
    try:
        session.cmd("ifup %s" % interface)
    finally:
        logging.debug(session.cmd_output("ifconfig -a"))
        session.close()


def execute_ttcp(test, vm, params):
    """
    Run ttcp between guest and host.

    :param vm: guest vm
    """
    remote_ip = params.get("vfio_remote_ip", "REMOTE_IP.EXAMPLE")
    remote_pwd = params.get("vfio_remote_passwd", "REMOTE_PWD.EXAMPLE")
    if remote_ip.count("EXAMPLE"):
        logging.debug("Please provider remote host for ttcp test.")
        return

    session1 = vm.wait_for_login()
    if session1.cmd_status("which ttcp"):
        session1.close()
        logging.debug("Did not find ttcp command on guest.SKIP...")
        return
    # Check connection first
    try:
        session1.cmd("ping -c 4 %s" % remote_ip)
    except Exception:
        test.fail("Couldn't connect to %s through %s"
                  % (remote_ip, vm.name))

    # Execute ttcp server on remote host
    ttcp_server = "ttcp -s -r -v -D -p5015"
    session1.sendline("ssh %s" % remote_ip)
    remote.handle_prompts(session1, "root", remote_pwd, r"[\#\$]\s*$",
                          timeout=30, debug=True)
    logging.debug("Executing ttcp server:%s", ttcp_server)
    session1.sendline(ttcp_server)
    # Another session for client
    session2 = vm.wait_for_login()
    ttcp_client = ("ttcp -s -t -v -D -p5015 -b65536 -l65536 -n1000 -f K %s"
                   % remote_ip)
    try:
        ttcp_s, ttcp_o = session2.cmd_status_output(ttcp_client)
        logging.debug(ttcp_o)
        if ttcp_s:
            test.fail("Run ttcp between %s and %s failed."
                      % (vm.name, remote_ip))
    finally:
        session1.close()
        session2.close()


def format_disk(test, vm, device, partsize):
    """
    Create a partition on given disk and check it.
    """
    if not vm.is_alive():
        vm.start()
    session = vm.wait_for_login()
    if session.cmd_status("ls %s" % device):
        test.fail("Can not find '%s' in guest." % device)
    else:
        if session.cmd_status("which parted"):
            logging.error("Did not find command 'parted' in guest, SKIP...")
            return

    try:
        partition = "%s1" % device
        if session.cmd_status("ls %s" % partition):
            utlv.mk_part(device, size=partsize, session=session)
        utlv.mkfs(partition, "ext4", session=session)
    except Exception as detail:
        test.fail("Create&format partition for '%s' failed: %s"
                  % (device, str(detail)))
    finally:
        logging.debug(session.cmd_output("parted -l"))
        session.close()


def cleanup_vm(vm_name=None):
    """
    Cleanup the vm.
    """
    try:
        if vm_name is not None:
            virsh.undefine(vm_name)
    except process.CmdError:
        pass


def test_nic_group(test, vm, params):
    """
    Try to attach device in iommu group to vm.

    1.Get original available interfaces before attaching.
    2.Attaching hostdev in iommu group to vm.
    3.Start vm and check it.
    4.Check added interface in vm.
    """
    pci_id = params.get("nic_pci_id", "ETH:PCI.EXAMPLE")
    if pci_id.count("EXAMPLE"):
        test.cancel("Invalid pci device id.")

    device_type = "Ethernet"
    nic_ip = params.get("nic_pci_ip")
    nic_mask = params.get("nic_pci_mask", "255.255.0.0")
    nic_gateway = params.get("nic_pci_gateway")
    attach_iface = "yes" == params.get("attach_iface", "no")
    vm_status = params.get("vm_status", "")
    ext_opt = params.get("attach_options", "")

    # Login vm to get interfaces before attaching pci device.
    if vm.is_dead():
        vm.start()
    before_pci_nics = vm.get_pci_devices("Ethernet")
    before_interfaces = vm.get_interfaces()
    logging.debug("Ethernet PCI devices before:%s",
                  before_pci_nics)
    logging.debug("Ethernet interfaces before:%s",
                  before_interfaces)
    if not vm_status == "running":
        vm.destroy()

    boot_order = int(params.get("boot_order", 0))
    prepare_devices(test, pci_id, device_type)
    try:
        if boot_order:
            utlv.alter_boot_order(vm.name, pci_id, boot_order)
        elif attach_iface:
            options = "hostdev " + pci_id + " " + ext_opt
            virsh.attach_interface(vm.name, options,
                                   debug=True, ignore_status=False)
        else:
            xmlfile = utlv.create_hostdev_xml(pci_id)
            virsh.attach_device(vm.name, xmlfile.xml,
                                flagstr="--config", debug=True,
                                ignore_status=False)
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm.name)
        logging.debug("VMXML with disk boot:\n%s", vmxml)
        iface_list = vmxml.get_iface_all()
        for node in list(iface_list.values()):
            if node.get('type') == 'hostdev':
                if "managed" in ext_opt:
                    if not node.get('managed') == "yes":
                        test.fail("Managed option can not"
                                  " be found in domain xml")
        if not vm.is_alive():
            vm.start()
    except (process.CmdError, virt_vm.VMStartError) as detail:
        cleanup_devices(pci_id, device_type)
        test.fail("New device does not work well: %s" % detail)

    # VM shouldn't login under boot order 1
    if boot_order:
        try:
            boot_timeout = int(params.get("boot_timeout", 60))
            vm.wait_for_login(timeout=boot_timeout)
            test.fail("Login vm successfully, but not expected.")
        except remote.LoginTimeoutError:
            logging.debug("Expected failure.")
            vm.destroy(gracefully=False)
            cleanup_devices(pci_id, device_type)
            return

    # Get devices in vm again after attaching
    after_pci_nics = vm.get_pci_devices("Ethernet")
    after_interfaces = vm.get_interfaces()
    logging.debug("Ethernet PCI devices after:%s",
                  after_pci_nics)
    logging.debug("Ethernet interfaces after:%s",
                  after_interfaces)
    new_pci = "".join(list(set(before_pci_nics) ^ set(after_pci_nics)))
    new_interface = "".join(list(set(before_interfaces) ^ set(after_interfaces)))
    try:
        if not new_pci or not new_interface:
            test.fail("Cannot find attached host device in vm.")
        # Config network for new interface
        config_network(test, vm, new_interface, nic_ip, nic_mask, nic_gateway)
        # Check interface
        execute_ttcp(test, vm, params)
    finally:
        if vm.is_alive():
            vm.destroy()
        cleanup_devices(pci_id, device_type)


def test_fibre_group(test, vm, params):
    """
    Try to attach device in iommu group to vm.

    1.Get original available disks before attaching.
    2.Attaching hostdev in iommu group to vm.
    3.Start vm and check it.
    4.Check added disk in vm.
    """
    pci_id = params.get("fibre_pci_id", "FIBRE:PCI.EXAMPLE")
    device_type = "Fibre"
    if pci_id.count("EXAMPLE"):
        test.cancel("Invalid pci device id.")
    disk_check = "yes" == params.get("fibre_pci_disk_check", "no")

    # Login vm to get disks before attaching pci device.
    if vm.is_dead():
        vm.start()
    before_pci_fibres = vm.get_pci_devices("Fibre")
    before_disks = vm.get_disks()
    logging.debug("Fibre PCI devices before:%s",
                  before_pci_fibres)
    logging.debug("Disks before:%s",
                  before_disks)
    vm.destroy()

    boot_order = int(params.get("boot_order", 0))
    prepare_devices(test, pci_id, device_type)
    try:
        if boot_order:
            utlv.alter_boot_order(vm.name, pci_id, boot_order)
        else:
            xmlfile = utlv.create_hostdev_xml(pci_id)
            virsh.attach_device(vm.name, xmlfile.xml,
                                flagstr="--config", debug=True,
                                ignore_status=False)
        logging.debug("VMXML with disk boot:\n%s", virsh.dumpxml(vm.name))
        vm.start()
    except (process.CmdError, virt_vm.VMStartError) as detail:
        cleanup_devices(pci_id, device_type)
        test.fail("New device does not work well: %s" % detail)

    # VM shouldn't login under boot order 1
    if boot_order:
        try:
            boot_timeout = int(params.get("boot_timeout", 60))
            vm.wait_for_login(timeout=boot_timeout)
            test.fail("Login vm successfully, but not expected.")
        except remote.LoginTimeoutError:
            logging.debug("Expected failure.")
            vm.destroy(gracefully=False)
            cleanup_devices(pci_id, device_type)
            return

    # Get devices in vm again after attaching
    after_pci_fibres = vm.get_pci_devices("Fibre")
    after_disks = vm.get_disks()
    logging.debug("Fibre PCI devices after:%s",
                  after_pci_fibres)
    logging.debug("Disks after:%s",
                  after_disks)
    new_pci = "".join(list(set(before_pci_fibres) ^ set(after_pci_fibres)))
    new_disk = "".join(list(set(before_disks) ^ set(after_disks)))
    try:
        if not new_pci:
            test.fail("Cannot find attached host device in vm.")
        if disk_check:
            after_disks = vm.get_disks()
            if not new_disk:
                test.fail("Cannot find attached host device in vm.")
            # Config disk for new disk device
            format_disk(test, vm, new_disk, "10M")
    finally:
        if vm.is_alive():
            vm.destroy()
        cleanup_devices(pci_id, device_type)


def test_win_fibre_group(test, vm, params):
    """
    Try to attach device in iommu group to vm.

    1.Get original available disks before attaching.
    2.Attaching hostdev in iommu group to vm.
    3.Start vm and check it.
    4.Check added disk in vm.
    """
    pci_id = params.get("fibre_pci_id", "FIBRE:PCI.EXAMPLE")
    device_type = "Fibre"
    if pci_id.count("EXAMPLE"):
        test.cancel("Invalid pci device id.")

    # Login vm to get disks before attaching pci device.
    if vm.is_dead():
        vm.start()
    before_disks = get_windows_disks(vm)
    logging.debug("Disks before:%s",
                  before_disks)
    vm.destroy()

    xmlfile = utlv.create_hostdev_xml(pci_id)
    prepare_devices(test, pci_id, device_type)
    try:
        virsh.attach_device(vm.name, xmlfile.xml,
                            flagstr="--config", debug=True,
                            ignore_status=False)
        vm.start()
    except (process.CmdError, virt_vm.VMStartError) as detail:
        cleanup_devices(pci_id, device_type)
        test.fail("New device does not work well: %s" % detail)

    # Get devices in vm again after attaching
    after_disks = get_windows_disks(vm)
    logging.debug("Disks after:%s",
                  after_disks)
    new_disk = "".join(list(set(before_disks) ^ set(after_disks)))
    try:
        if not new_disk:
            test.fail("Cannot find attached host device in vm.")
        # TODO: Support to configure windows partition
    finally:
        if vm.is_alive():
            vm.destroy()
        cleanup_devices(pci_id, device_type)


def test_nic_fibre_group(test, vm, params):
    """
    Try to attach nic and fibre device at same time in iommu group to vm.

    1.Get original available interfaces&disks before attaching.
    2.Attaching hostdev in iommu group to vm.
    3.Start vm and check it.
    4.Check added interface&disk in vm.
    """
    nic_pci_id = params.get("nic_pci_id", "ETH:PCI.EXAMPLE")
    fibre_pci_id = params.get("fibre_pci_id", "FIBRE:PCI.EXAMPLE")
    if nic_pci_id.count("EXAMPLE"):
        test.cancel("Invalid Ethernet pci device id.")
    if fibre_pci_id.count("EXAMPLE"):
        test.cancel("Invalid Fibre pci device id.")
    disk_check = "yes" == params.get("fibre_pci_disk_check", "no")

    nic_ip = params.get("nic_pci_ip")
    nic_mask = params.get("nic_pci_mask", "255.255.0.0")
    nic_gateway = params.get("nic_pci_gateway")

    # Login vm to get interfaces before attaching pci device.
    if vm.is_dead():
        vm.start()
    before_pci_nics = vm.get_pci_devices("Ethernet")
    before_interfaces = vm.get_interfaces()
    before_pci_fibres = vm.get_pci_devices("Fibre")
    before_disks = vm.get_disks()
    logging.debug("Ethernet PCI devices before:%s",
                  before_pci_nics)
    logging.debug("Ethernet interfaces before:%s",
                  before_interfaces)
    logging.debug("Fibre PCI devices before:%s",
                  before_pci_fibres)
    logging.debug("Disks before:%s",
                  before_disks)
    vm.destroy()

    prepare_devices(test, nic_pci_id, "Ethernet")
    prepare_devices(test, fibre_pci_id, "Fibre")
    try:
        nicxmlfile = utlv.create_hostdev_xml(nic_pci_id)
        virsh.attach_device(vm.name, nicxmlfile.xml,
                            flagstr="--config", debug=True,
                            ignore_status=False)
        fibrexmlfile = utlv.create_hostdev_xml(fibre_pci_id)
        virsh.attach_device(vm.name, fibrexmlfile.xml,
                            flagstr="--config", debug=True,
                            ignore_status=False)
        vm.start()
    except (process.CmdError, virt_vm.VMStartError) as detail:
        cleanup_devices(nic_pci_id, "Ethernet")
        cleanup_devices(fibre_pci_id, "Fibre")
        test.fail("New device does not work well: %s" % detail)

    # Get nic devices in vm again after attaching
    after_pci_nics = vm.get_pci_devices("Ethernet")
    after_interfaces = vm.get_interfaces()
    logging.debug("Ethernet PCI devices after:%s",
                  after_pci_nics)
    logging.debug("Ethernet interfaces after:%s",
                  after_interfaces)
    # Get disk devices in vm again after attaching
    after_pci_fibres = vm.get_pci_devices("Fibre")
    after_disks = vm.get_disks()
    logging.debug("Fibre PCI devices after:%s",
                  after_pci_fibres)
    logging.debug("Disks after:%s",
                  after_disks)

    new_pci_nic = "".join(list(set(before_pci_nics) ^ set(after_pci_nics)))
    new_interface = "".join(list(set(before_interfaces) ^ set(after_interfaces)))
    new_pci_fibre = "".join(list(set(before_pci_fibres) ^ set(after_pci_fibres)))
    new_disk = "".join(list(set(before_disks) ^ set(after_disks)))

    try:
        if not new_pci_nic or not new_interface:
            test.fail("Cannot find attached host device in vm.")
        # Config network for new interface
        config_network(test, vm, new_interface, nic_ip, nic_mask, nic_gateway)
        # Check interface
        execute_ttcp(test, vm, params)

        if not new_pci_fibre:
            test.fail("Cannot find attached host device in vm.")
        if disk_check:
            if not new_disk:
                test.fail("Cannot find attached host device in vm.")
            # Config disk for new disk device
            format_disk(test, vm, new_disk, "10M")
    finally:
        if vm.is_alive():
            vm.destroy()
        cleanup_devices(nic_pci_id, "Ethernet")
        cleanup_devices(fibre_pci_id, "Fibre")


def test_nic_single(test, vm, params):
    """
    Try to attach device in iommu group to vm with adding only this
    device to iommu group.

    1.Get original available interfaces before attaching.
    2.Attaching hostdev in iommu group to vm.
    3.Start vm and check it.
    4.Check added interface in vm.
    """
    pci_id = params.get("nic_pci_id", "ETH:PCI.EXAMPLE")
    if pci_id.count("EXAMPLE"):
        test.cancel("Invalid pci device id.")

    device_type = "Ethernet"
    nic_ip = params.get("nic_pci_ip")
    nic_mask = params.get("nic_pci_mask", "255.255.0.0")
    nic_gateway = params.get("nic_pci_gateway")

    # Login vm to get interfaces before attaching pci device.
    if vm.is_dead():
        vm.start()
    before_pci_nics = vm.get_pci_devices("Ethernet")
    before_interfaces = vm.get_interfaces()
    logging.debug("Ethernet PCI devices before:%s",
                  before_pci_nics)
    logging.debug("Ethernet interfaces before:%s",
                  before_interfaces)
    vm.destroy()

    # Add only this device to corresponding iommu group
    prepare_devices(test, pci_id, device_type, only=True)
    try:
        xmlfile = utlv.create_hostdev_xml(pci_id)
        virsh.attach_device(vm.name, xmlfile.xml,
                            flagstr="--config", debug=True,
                            ignore_status=False)
        vm.start()
        # Start successfully, but not expected.
        vm.destroy(gracefully=False)
        cleanup_devices(pci_id, device_type)
        test.fail("Start vm succesfully after attaching single "
                  "device to iommu group.Not expected.")
    except (process.CmdError, virt_vm.VMStartError) as detail:
        logging.debug("Expected:New device does not work well: %s" % detail)

    # Reattaching all devices in iommu group
    prepare_devices(test, pci_id, device_type)
    try:
        vm.start()
    except Exception as detail:
        cleanup_devices(pci_id, device_type)
        test.fail("Start vm failed after attaching all"
                  "device to iommu group:%s" % detail)

    # Get devices in vm again after attaching
    after_pci_nics = vm.get_pci_devices("Ethernet")
    after_interfaces = vm.get_interfaces()
    logging.debug("Ethernet PCI devices after:%s",
                  after_pci_nics)
    logging.debug("Ethernet interfaces after:%s",
                  after_interfaces)
    new_pci = "".join(list(set(before_pci_nics) ^ set(after_pci_nics)))
    new_interface = "".join(list(set(before_interfaces) ^ set(after_interfaces)))
    try:
        if not new_pci or not new_interface:
            test.fail("Cannot find attached host device in vm.")
        # Config network for new interface
        config_network(test, vm, new_interface, nic_ip, nic_mask, nic_gateway)
        # Check interface
        execute_ttcp(test, vm, params)
    finally:
        if vm.is_alive():
            vm.destroy()
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
    test_type = params.get("test_type")

    if vm.is_alive():
        vm.destroy()
    new_vm_name = "%s_vfiotest" % vm.name
    if not utlv.define_new_vm(vm.name, new_vm_name):
        test.fail("Define new vm failed.")

    try:
        new_vm = libvirt_vm.VM(new_vm_name, vm.params, vm.root_dir,
                               vm.address_cache)

        if "yes" == params.get("primary_boot", "no"):
            params['boot_order'] = 1
        else:
            params['boot_order'] = 0

        testcase = globals()["test_%s" % test_type]
        testcase(test, new_vm, params)
    finally:
        if new_vm.is_alive():
            new_vm.destroy()
        cleanup_vm(new_vm.name)
