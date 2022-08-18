import logging
import os
import re

from avocado.utils import linux_modules

from virttest import data_dir
from virttest import libvirt_version
from virttest import libvirt_xml
from virttest import utils_linux_modules as Module
from virttest import utils_disk
from virttest import utils_misc
from virttest import virt_vm
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.disk import Disk
from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_virtio

LOG = logging.getLogger('avocado.' + __name__)


def check_virtio_iommu_support(test, vm):
    """
    Check that the kernel of both host and guest supports virtio-iommu.

    :param test: Test object
    :param vm: vm object
    """
    global load_virtio_iommu

    try:
        session = None

        # VIRTIO_IOMMU could be Y or M in Host
        if Module.kconfig_is_not_set("CONFIG_VIRTIO_IOMMU"):
            test.cancel("Host kernel doesn't support VIRTIO_IOMMU")
        else:
            # Load Host virtio-iommu module if necessary
            if not linux_modules.module_is_loaded("virtio-iommu") and \
                    Module.kconfig_is_module("CONFIG_VIRTIO_IOMMU"):
                LOG.info("Load virtio-iommu module.")
                load_virtio_iommu = True
                utils_misc.wait_for(
                    lambda: linux_modules.load_module("virtio-iommu"),
                    timeout=30, ignore_errors=True)

        # VIRTIO_IOMMU should be Y in Guest
        if not vm.is_alive():
            vm.start()
        session = vm.wait_for_login()
        if not Module.kconfig_is_builtin("CONFIG_VIRTIO_IOMMU", session):
            test.cancel("Guest kernel doesn't support VIRTIO_IOMMU")
    except Exception as e:
        test.error("Test env error: {}.".format(str(e)))
    finally:
        if session is not None:
            session.close()
        if vm.is_alive():
            vm.destroy()


def set_device(params, vmxml, device_type, backend_path):
    """
    Set Test device that protected by IOMMU.

    :param params: Test parameters
    :param vmxml: Domain vmxml
    :param device_type: Type of test device
    :param backend_path: The backend path of test device
    """
    def set_disk_device(params, vmxml, backend_path):
        """
        Set Test device that protected by IOMMU.

        :param params: Test parameters
        :param vmxml: Domain vmxml
        :param backend_path: The backend path of test device
        """
        LOG.info("Add disk to vm.")
        image_path = backend_path
        disk_dict = eval(params.get("disk_dict", "{}"))
        # Create test disk image
        disk_dict.update({"source": {'attrs': {'file': image_path}}})

        new_disk = Disk()
        new_disk.setup_attrs(**disk_dict)
        LOG.debug("New disk xml is {}".format(new_disk))
        libvirt.add_vm_device(vmxml, new_disk)

    if device_type == "disk":
        set_disk_device(params, vmxml, backend_path)

    # TODO: Other test device


def get_device_pci(vm_name, device_type):
    """
    Get pci info of a device

    :param vm_name: Guest name
    :param device_type: Type of test device

    :return: device domain, device pci
    """
    cur_xml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    LOG.debug("cur_xml is {}".format(cur_xml))

    if device_type == "disk":
        device = "disk"

    device_xml = cur_xml.get_devices(device)[-1]
    LOG.debug("Device is {}".format(device_xml))
    # Remove "0x" prefix because pci info doesn't include it.
    device_domain = device_xml.address.attrs.get("domain")[2:]
    device_bus = device_xml.address.attrs.get("bus")[2:]
    device_slot = device_xml.address.attrs.get("slot")[2:]
    device_func = device_xml.address.attrs.get("function")[2:]

    device_pci = "{}:{}.{}".format(device_bus, device_slot, device_func)
    LOG.debug("Device domain is {}, pci is {}".format(device_domain, device_pci))

    return device_domain, device_pci


def test_device_io(test, session, vm_name, device_type):
    """
    Test guest device that protected by iommu

    :param test: Test object
    :param session: Guest session
    :param vm_name: Guest name
    :param device_type: Type of test device
    """
    cur_xml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    LOG.debug("cur_xml is {}".format(cur_xml))

    if device_type == "disk":
        target_device = cur_xml.get_devices("disk")[-1].target.get("dev")
        if target_device is None:
            test.error("Failed to get guest target device.")
        LOG.debug("Target_device is {}".format(target_device))
        utils_disk.dd_data_to_vm_disk(session, target_device, bs="1M",
                                      count="100")


def check_iommu_group(session, pci_domain, pci_addr, iommu_group):
    """
    Check if pci_addr is allocated at iommu_group

    :param session: Guest session
    :param pci_domain: PCI Domain
    :param pci_addr: PCI addr to check
    :param iommu_group: Iommu group to check

    :return: ls cmd exit status
    """
    sys_iommu_path = "/sys/kernel/iommu_groups/"

    check_path = os.path.join(sys_iommu_path, iommu_group[0],
                              "devices", "{}:{}".format(pci_domain, pci_addr))
    LOG.debug("Check iommu path: {}".format(check_path))
    return session.cmd_status("ls '{}'".format(check_path))


def run(test, params, env):
    """
    Test iommu device

    Test libvirt iommu device works well.
    1. Prepare test env and check kernel support
    2. Add iommu device to guest xml.
    3. Enable iommu for some devices.
    4. Check guest iommu info.
    5. Check the functionality of guest devices that protected by iommu.

    :param test: Test object
    :param params: Test parameters
    :param env: Test environment
    """
    libvirt_version.is_libvirt_feature_supported(params)
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    start_vm = "yes" == params.get("start_vm", "no")
    # Iommu params
    iommu_model = params.get("model", "virtio")
    virtio_iommu_dict = eval(params.get("virtio_iommu_dict", ""))
    # Test device params
    device_type = params.get("device_type", "disk")

    # Flag the test device backend
    device_backend_path = None
    # Flag loading virtio_iommu module
    load_virtio_iommu = False

    if iommu_model == "virtio":
        driver_name = "virtio-iommu"
        check_virtio_iommu_support(test, vm)

    if device_type == "disk":
        device_backend_path = data_dir.get_data_dir() + "/new_image"
        LOG.debug("Test device backend is: {}".format(device_backend_path))
        libvirt.create_local_disk("file", path=device_backend_path, disk_format="qcow2")

    # Do backup
    backup_xml = libvirt_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    try:
        # Setup Guest
        try:
            # Add Iommu device
            LOG.info("Add iommu to vm.")
            libvirt_virtio.add_iommu_dev(vm, virtio_iommu_dict)
            # Add device that protected by iommu
            vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
            set_device(params, vmxml, device_type, device_backend_path)
            LOG.debug("vmxml is \n{}".format(vmxml))
            # Start guest with test devices
            vm.start()
            session = vm.wait_for_login()
        except libvirt_xml.xcepts.LibvirtXMLError as xml_error:
            test.fail("Failed to defince VM:\n{}".format(xml_error))
        except virt_vm.VMStartError as details:
            test.fail("Failed to start guest: {}".format(details))
        except Exception as e:
            test.fail("{}".format(str(e)))

        # Check iommu in qemu cmdline
        check_qemu_pattern = rf'-device {{"driver":"{driver_name}"'
        if not libvirt.check_qemu_cmd_line(check_qemu_pattern):
            test.fail("Qemu failed to create virtio-iommu device.")

        dmesg_iommu = session.cmd_output("dmesg | grep 'Adding to iommu group'")
        LOG.debug("Guest dmesg iommu info is:\n{}".format(dmesg_iommu))
        if len(dmesg_iommu) == 0:
            test.error("Can't find iommu add info in guest dmesg")

        # Get guest test device pci info
        (device_domain, device_pci) = get_device_pci(vm_name, device_type)
        iommu_group = re.findall(r'virtio-pci {}:{}: Adding to iommu group (\d+)'
                                 .format(device_domain, device_pci), dmesg_iommu)
        LOG.debug("Test device iommu group is {}".format(iommu_group))
        if len(iommu_group) != 1:
            test.error("Get test device iommu group error. \n"
                       "Expected get 1 test device iommu group info")

        # Check if test device is allocated at correct iommu group
        if check_iommu_group(session, device_domain, device_pci, iommu_group):
            test.fail("Test PCI device {} was not allocated at correct "
                      "iommu_group".format(device_pci))

        # I/O Test
        test_device_io(test, session, vm_name, device_type)

    finally:
        if session:
            session.close()
        if vm.is_alive():
            vm.destroy(gracefully=False)
        backup_xml.sync()

        # Recover Host configure
        if load_virtio_iommu:
            utils_misc.wait_for(
                lambda: linux_modules.unload_module("virtio-iommu"),
                timeout=30, ignore_errors=True)

        # Delete created test device backend
        if device_backend_path is not None and \
                os.path.exists(device_backend_path):
            os.remove(device_backend_path)
