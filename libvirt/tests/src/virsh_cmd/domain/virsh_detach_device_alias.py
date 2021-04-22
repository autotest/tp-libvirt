import uuid
import time
import logging

from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.utils_disk import get_scsi_info

from avocado.utils import process


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

    device_alias = "ua-" + str(uuid.uuid4())

    def get_usb_info():
        """
        Get local host usb info

        :return: usb verndor and product id
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

    try:
        dump_option = ""
        if "--config" in detach_options:
            dump_option = "--inactive"

        # Attach xml to domain
        logging.info("Attach xml is %s" % process.run("cat %s" % device_xml.xml).stdout_text)
        virsh.attach_device(vm_name, device_xml.xml, flagstr=detach_options,
                            debug=True, ignore_status=False)
        domxml_at = virsh.dumpxml(vm_name, dump_option, debug=True).stdout.strip()
        if detach_check_xml not in domxml_at:
            test.error("Can not find %s in domxml after attach" % detach_check_xml)

        # Detach xml with alias
        result = virsh.detach_device_alias(vm_name, device_alias, detach_options, debug=True)
        time.sleep(10)
        libvirt.check_exit_status(result)
        domxml_dt = virsh.dumpxml(vm_name, dump_option, debug=True).stdout.strip()
        if detach_check_xml in domxml_dt:
            test.fail("Still can find %s in domxml" % detach_check_xml)
    finally:
        backup_xml.sync()
        if hostdev_type == "scsi":
            libvirt.delete_scsi_disk()
