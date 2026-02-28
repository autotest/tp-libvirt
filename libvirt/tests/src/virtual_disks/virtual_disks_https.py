import re
import logging
from urllib.request import urlopen

from virttest import virsh
from virttest import libvirt_version

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt
from virttest.utils_libvirtd import Libvirtd
from virttest.utils_config import LibvirtQemuConfig

LOG = logging.getLogger('avocado.' + __name__)


def get_latest_fedora_version(base_url):
    """
    Get the latest Fedora version from the releases directory.

    :param base_url: Base URL of Fedora releases directory
    :return: Latest version number as string
    """
    try:
        html = urlopen(base_url, timeout=10).read().decode('utf-8')
        vers = re.findall(r'>(\d+)/<', html)
        if not vers:
            raise RuntimeError(f"No version directories found at {base_url}")
        LOG.info(f"Found the latest Fedora version: {max(vers, key=int)}")
        return max(vers, key=int)
    except Exception as e:
        raise Exception(f"Failed to get latest version from {base_url}: {str(e)}")


def get_fedora_iso_build(base_url, version, arch="x86_64", server_type="Server"):
    """
    Get the exact build number from the ISO directory listing.

    :param base_url: Base URL of Fedora releases directory
    :param version: Fedora version number
    :param arch: System architecture, default is "x86_64"
    :param server_type: Server type, default is "Server"
    :return: Build number as string (e.g., "1.6")
    """
    iso_dir_url = f"{base_url}{version}/{server_type}/{arch}/iso/"
    try:
        html = urlopen(iso_dir_url, timeout=10).read().decode('utf-8')
        pattern = rf"Fedora-{server_type}-netinst-{arch}-{version}-([\d.]+)\.iso"
        match = re.search(pattern, html)
        if not match:
            raise RuntimeError(f"Cannot find netinst ISO filename in directory listing at {iso_dir_url}")
        LOG.info(f"Found the Fedora build number: {match.group(1)}")
        return match.group(1)
    except Exception as e:
        raise Exception(f"Failed to get build number from {iso_dir_url}: {str(e)}")


def get_latest_fedora_iso_url(base_url, arch="x86_64", server_type="Server"):
    """
    Get the latest Fedora Server netinst ISO path or URL.

    :param base_url: Base URL of Fedora releases
    :param arch: System architecture, default is "x86_64"
    :param server_type: Server type, default is "Server"
    :return: ISO path
    """
    version = get_latest_fedora_version(base_url)
    build = get_fedora_iso_build(base_url, version, arch, server_type)

    iso_path = (
        f"/fedora/linux/releases/{version}/{server_type}/{arch}/iso/"
        f"Fedora-{server_type}-netinst-{arch}-{version}-{build}.iso"
    )
    return iso_path


def run(test, params, env):
    """
    Start vm and hotplug/unplug https network disk with different options.

    1. Start vm with/without https network disk.
    2. Hot-unplug https network disk.
    3. Hotplug the https network disk.
    """
    def setup_test():
        """
        Prepare a https network disk test environment.

        :param qemu_config: return the qemu config.
        """
        qemu_config = LibvirtQemuConfig()
        qemu_config.storage_use_nbdkit = 1
        Libvirtd('virtqemud').restart()
        return qemu_config

    def prepare_disk():
        """
        Prepare the https network disk.

        :param disk_obj: return the disk object.
        """
        network_device = params.get("network_device")
        iso_path = get_latest_fedora_iso_url(base_url, arch="x86_64", server_type="Server")
        disk_dict = eval(params.get("disk_dict", "{}") % (network_device, iso_path))
        disk_obj = libvirt_vmxml.create_vm_device_by_type("disk", disk_dict)
        if not with_hotplug:
            libvirt.add_vm_device(vmxml, disk_obj)
        vmxml.sync()
        return disk_obj

    def check_result(disk_in_vm=True):
        """
        Check the test result.

        :param disk_in_vm: boolean value to make sure if disk in vm.
        """
        if disk_in_vm:
            xml_after_adding_device = vm_xml.VMXML.new_from_dumpxml(vm_name)
            libvirt_vmxml.check_guest_xml_by_xpaths(xml_after_adding_device, expected_xpaths)
            libvirt_vmxml.check_guest_xml(vm_name, cookie_in_dumpxml,
                                          option=" --security-info")
        else:
            domblklist_result = virsh.domblklist(vm_name, debug=True).stdout_text.strip()
            if target_disk in domblklist_result:
                test.fail("The target disk %s can't be detached from guest." % target_disk)

    libvirt_version.is_libvirt_feature_supported(params)
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    target_disk = params.get("target_disk")
    expected_xpaths = eval(params.get("expected_xpaths"))
    cookie_in_dumpxml = params.get("cookie_in_dumpxml")
    with_hotplug = "yes" == params.get("with_hotplug", "no")
    base_url = params.get("base_url")

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    backup_vmxml = vmxml.copy()

    try:
        qemu_config = setup_test()
        disk_dev = prepare_disk()
        test.log.info("TEST_STEP1: Start the guest.")
        if not vm.is_alive():
            vm.start()
        vm.wait_for_login().close()
        test.log.debug("The current guest xml is: %s", virsh.dumpxml(vm_name).stdout_text)
        disk_in_vm = not with_hotplug
        check_result(disk_in_vm)
        if with_hotplug:
            test.log.info("TEST_STEP2: Hotplug the disk.")
            virsh.attach_device(vm_name, disk_dev.xml, debug=True, ignore_status=False)
            check_result()
            test.log.info("TEST_STEP3: Hot-unplug the disk.")
            virsh.detach_device(vm_name, disk_dev.xml, debug=True, ignore_status=False)
            check_result(disk_in_vm=False)
    finally:
        backup_vmxml.sync()
        qemu_config.restore()
        Libvirtd('virtqemud').restart()
