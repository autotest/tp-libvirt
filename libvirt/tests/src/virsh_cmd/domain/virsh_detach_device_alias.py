import os
import uuid
import logging as log

from virttest import data_dir
from virttest import virsh
from virttest import utils_misc
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.disk import Disk
from virttest.libvirt_xml.devices.watchdog import Watchdog
from virttest.utils_test import libvirt
from virttest.utils_disk import get_scsi_info

from avocado.utils import process


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test detach-device-alias command with
    --config, --live, --current

    1. Test hostdev device detach
    2. Test scsi controller device detach
    3. Test redirect device detach
    4. Test channel devices detach
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    detach_options = params.get("detach_alias_options", "")
    detach_check_xml = params.get("detach_check_xml")
    # hostdev device params
    hostdev_type = params.get("detach_hostdev_type", "")
    hostdev_managed = params.get("detach_hostdev_managed")
    # controller params
    contr_type = params.get("detach_controller_type")
    contr_model = params.get("detach_controller_mode")
    # redirdev params
    redir_type = params.get("detach_redirdev_type")
    redir_bus = params.get("detach_redirdev_bus")
    # channel params
    channel_type = params.get("detach_channel_type")
    channel_target = eval(params.get("detach_channel_target", "{}"))
    # virtual disk params
    virtual_disk_type = params.get("detach_virtual_disk_type")
    virtual_disk_dict = eval(params.get("virtual_disk_dict", "{}"))
    # watchdog params
    watchdog_type = params.get("detach_watchdog_type")
    watchdog_dict = eval(params.get('watchdog_dict', '{}'))

    device_alias = "ua-" + str(uuid.uuid4())

    def check_detached_xml_noexist():
        """
        Check detached xml does not exist in the guest dumpxml

        :return: True if it does not exist, False if still exists
        """
        domxml_dt = virsh.dumpxml(vm_name, dump_option).stdout_text.strip()
        if detach_check_xml not in domxml_dt:
            return True
        else:
            return False

    def get_usb_info():
        """
        Get local host usb info

        :return: usb vendor and product id
        """
        install_cmd = process.run("yum install usbutils* -y", shell=True)
        result = process.run("lsusb|awk '{print $6\":\"$2\":\"$4}'", shell=True)
        if not result.exit_status:
            return result.stdout_text.rstrip(':')
        else:
            test.error("Can not get usb hub info for testing")

    # backup xml
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()
    device_xml = None
    attach_device = True

    if not vm.is_alive():
        vm.start()
    # wait for vm start successfully
    vm.wait_for_login()

    if hostdev_type:
        if hostdev_type in ["usb", "scsi"]:
            if hostdev_type == "usb":
                pci_id = get_usb_info()
            elif hostdev_type == "scsi":
                source_disk = libvirt.create_scsi_disk(scsi_option="",
                                                       scsi_size="8")
                pci_id = get_scsi_info(source_disk)
            device_xml = libvirt.create_hostdev_xml(pci_id=pci_id,
                                                    dev_type=hostdev_type,
                                                    managed=hostdev_managed,
                                                    alias=device_alias)
        else:
            test.error("Hostdev type %s not handled by test."
                       " Please check code." % hostdev_type)
    if contr_type:
        controllers = vmxml.get_controllers(contr_type)
        contr_index = len(controllers) + 1
        contr_dict = {"controller_type": contr_type,
                      "controller_model": contr_model,
                      "controller_index": contr_index,
                      "contr_alias": device_alias}
        device_xml = libvirt.create_controller_xml(contr_dict)
        detach_check_xml = detach_check_xml % contr_index

    if redir_type:
        device_xml = libvirt.create_redirdev_xml(redir_type, redir_bus, device_alias)

    if channel_type:
        channel_params = {'channel_type_name': channel_type}
        channel_params.update(channel_target)
        device_xml = libvirt.create_channel_xml(channel_params, device_alias)
    if virtual_disk_type:
        image_path = data_dir.get_data_dir() + '/new_image'
        libvirt.create_local_disk("file", path=image_path, disk_format="qcow2")

        virtual_disk_dict.update({"alias": {"name": device_alias},
                                  "source": {'attrs': {'file': image_path}}})

        new_disk = Disk()
        new_disk.setup_attrs(**virtual_disk_dict)
        libvirt.add_vm_device(vmxml, new_disk)

        if not vm.is_alive():
            vm.start()
        vm.wait_for_login().close()

        attach_device = False

    if watchdog_type:
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        vmxml.remove_all_device_by_type('watchdog')

        device_xml_file = Watchdog()
        device_xml_file.update({"alias": {"name": device_alias}})
        device_xml_file.setup_attrs(**watchdog_dict)
        vmxml.devices = vmxml.devices.append(device_xml_file)
        vmxml.xmltreefile.write()
        vmxml.sync()

        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        logging.debug('The vmxml after attached watchdog is:%s', vmxml)

        if not vm.is_alive():
            vm.start()
        vm.wait_for_login().close()

        attach_device = False

    try:
        dump_option = ""
        wait_event = True
        if "--config" in detach_options:
            dump_option = "--inactive"
            wait_event = False

        # Attach xml to domain
        if attach_device:
            logging.info("Attach xml is %s" % process.run("cat %s" % device_xml.xml).stdout_text)
            virsh.attach_device(vm_name, device_xml.xml, flagstr=detach_options,
                                debug=True, ignore_status=False)

        domxml_at = virsh.dumpxml(vm_name, dump_option, debug=True).stdout.strip()
        if detach_check_xml not in domxml_at:
            test.error("Can not find %s in domxml after attach" % detach_check_xml)

        # Detach xml with alias
        result = virsh.detach_device_alias(vm_name, device_alias, detach_options,
                                           wait_for_event=wait_event,
                                           event_timeout=20,
                                           debug=True)
        libvirt.check_exit_status(result)
        if not utils_misc.wait_for(check_detached_xml_noexist,
                                   60,
                                   step=2,
                                   text="Repeatedly search guest dumpxml with detached xml"):
            test.fail("Still can find %s in domxml" % detach_check_xml)
    finally:
        backup_xml.sync()
        if hostdev_type == "scsi":
            libvirt.delete_scsi_disk()
        if virtual_disk_type and os.path.exists(image_path):
            os.remove(image_path)
