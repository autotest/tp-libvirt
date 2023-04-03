import logging
import platform
import shutil
import time

from avocado.utils import process

from virttest import utils_disk
from virttest import utils_misc
from virttest import virsh
from virttest import virt_vm

from virttest.libvirt_xml import vm_xml, xcepts
from virttest.utils_libvirt import libvirt_disk

LOG = logging.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test nvme virtual.

    1.Prepare a vm with nvme type disk
    2.Attach the virtual disk to the vm
    3.Start vm
    4.Check the disk in vm
    5.Detach nvme device
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    def get_new_disks(old_partitions):
        """
        Get new virtual disks in VM after disk plug.

        :param old_partitions: already existing partitions in VM
        :return: New disks/partitions in VM
        """
        session = None
        try:
            session = vm.wait_for_login()
            if platform.platform().count('ppc64'):
                time.sleep(10)
            added_partitions = utils_disk.get_added_parts(session, old_partitions)
            LOG.debug("Newly added partition(s) is: %s", added_partitions)
            return added_partitions
        except Exception as err:
            test.fail("Error happens when get new disk: %s" % str(err))
        finally:
            if session:
                session.close()

    def get_usable_nvme_pci_address():
        """
        Get usable nvme device pci address
        return: usable nvme pci address in Dict
        """
        lspci_output = process.run(
            "lspci | grep -i 'Non-Volatile memory controller'",
            timeout=10, ignore_status=True, verbose=False, shell=True)
        # Expected output look something like below:
        # 17:00.0 Non-Volatile memory controller
        if lspci_output.exit_status != 0:
            test.cancel("Failed to execute lspci on host with output:{}".format(lspci_output))
        pci_address = lspci_output.stdout_text.strip().split()[0].split(":")
        address_dict = {}
        address_dict.update({'domain': '0x0000'})
        address_dict.update({'bus': '0x{}'.format(pci_address[0])})
        address_dict.update({'slot': '0x{}'.format(pci_address[1].split('.')[0])})
        address_dict.update({'function': '0x{}'.format(pci_address[1].split('.')[1])})
        return address_dict

    def create_customized_disk(params, pci_address):
        """
        Create one customized disk with related attributes

        :param params: dict wrapped with params
        :param pci_address: pci address in dict format
        """
        type_name = params.get("type_name")
        disk_device = params.get("device_type")
        device_target = params.get("target_dev")
        device_bus = params.get("target_bus")
        device_format = params.get("target_format")

        customized_disk = libvirt_disk.create_primitive_disk_xml(
            type_name, disk_device,
            device_target, device_bus,
            device_format, None, None)

        source_dict = eval(params.get("source_attrs"))
        disk_src_dict = {"attrs": source_dict}
        disk_source = customized_disk.new_disk_source(**disk_src_dict)
        disk_source.address = pci_address
        customized_disk.source = disk_source
        LOG.debug("create customized xml: %s", customized_disk)
        return customized_disk

    hotplug = "yes" == params.get("virt_device_hotplug")
    pkgs_host = params.get("pkgs_host", "")

    # Get disk partitions info before hot/cold plug virtual disk
    if vm.is_dead():
        vm.start()
    session = vm.wait_for_login()
    old_partitions = utils_disk.get_parts_list(session)
    session.close()
    vm.destroy(gracefully=False)

    # Backup vm xml
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    vmxml = vmxml_backup.copy()

    try:
        # install essential package in host
        if not shutil.which('lspci'):
            test.error("package {} not installed, please install them before testing".format(pkgs_host))

        pci_addr_in_dict = get_usable_nvme_pci_address()

        # Delete partitions if exist on nvme device
        process.run("sfdisk --delete  /dev/nvme[0-9]n[0-9]",
                    timeout=10, ignore_status=True, verbose=True, shell=True)

        device_obj = create_customized_disk(params, pci_addr_in_dict)
        if not hotplug:
            vmxml.add_device(device_obj)
            vmxml.sync()
        vm.start()
        vm.wait_for_login().close()
        if hotplug:
            virsh.attach_device(vm_name, device_obj.xml, flagstr="--live",
                                ignore_status=False, debug=True)
    except virt_vm.VMStartError as details:
        test.fail("VM failed to start."
                  "Error: %s" % str(details))
    except xcepts.LibvirtXMLError as xml_error:
        test.fail("VM failed to define"
                  "Error: %s" % str(xml_error))
    else:
        utils_misc.wait_for(lambda: get_new_disks(old_partitions), 20)
        new_disks = get_new_disks(old_partitions)
        if len(new_disks) != 1:
            test.fail("Attached 1 virtual disk but got %s." % len(new_disks))
        new_disk = new_disks[0]
        if platform.platform().count('ppc64'):
            time.sleep(10)
        if not libvirt_disk.check_virtual_disk_io(vm, new_disk):
            test.fail("Cannot operate the newly added disk in vm.")
        virsh.detach_device(vm_name, device_obj.xml, flagstr="--live",
                            debug=True, ignore_status=False)
    finally:
        if vm.is_alive():
            vm.destroy(gracefully=False)
        # Restoring vm
        vmxml_backup.sync()
