import re
import logging as log

from virttest.libvirt_xml.devices.sound import Sound
from virttest.libvirt_xml.vm_xml import VMXML
from virttest import virsh

from virttest import libvirt_version


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test the sound virtual devices
    1. prepare a guest with different sound devices
    2. check whether the guest can be started
    3. check the xml and qemu cmd line
    """
    # Sound element supported since 0.4.3.
    if not libvirt_version.version_compare(0, 4, 3):
        test.cancel("Sound device is not supported "
                    "on current version.")
    # Codec sub-element supported since 0.9.13
    codec_type = params.get("codec_type", None)
    if codec_type and not libvirt_version.version_compare(0, 9, 13):
        test.cancel("codec sub-element is not supported "
                    "on current version.")

    def check_dumpxml():
        """
        Check whether the added devices are shown in the guest xml
        """
        pattern = "<sound model=\"%s\">" % sound_model
        # Check sound model
        xml_after_adding_device = VMXML.new_from_dumpxml(vm_name)
        if pattern not in str(xml_after_adding_device):
            test.fail("Can not find the %s sound device xml "
                      "in the guest xml file." % sound_model)
        # Check codec type
        if codec_type:
            pattern = "<codec type=\"%s\" />" % codec_type
            if pattern not in str(xml_after_adding_device):
                test.fail("Can not find the %s codec xml for sound dev "
                          "in the guest xml file." % codec_type)

        if sound_model == "ich9":
            sound_devices = xml_after_adding_device.get_devices("sound")
            for device in sound_devices:
                if device.model_type == "ich9":
                    check_device_address_slot(device, expected_address_slot_value)

    def check_qemu_cmd_line():
        """
        Check whether the added devices are shown in the qemu cmd line
        """
        if not vm.get_pid():
            test.fail('VM pid file missing.')
        with open('/proc/%s/cmdline' % vm.get_pid()) as cmdline_file:
            cmdline = cmdline_file.read()
        # Check sound model
        if sound_model == "ac97":
            pattern = r"-device.*AC97"
        elif sound_model == "ich6":
            pattern = r"-device.*intel-hda"
        else:
            pattern = r"-device.*ich9-intel-hda"
        if not re.search(pattern, cmdline):
            test.fail("Can not find the %s sound device "
                      "in qemu cmd line." % sound_model)
        # Check codec type
        if sound_model in ["ich6", "ich9"]:
            if codec_type == "micro":
                pattern = r"-device.*hda-micro"
            else:
                # Duplex is default in qemu cli even codec not set
                # But before 0.9.13, no codec_type so no default
                if libvirt_version.version_compare(0, 9, 13):
                    pattern = r"-device.*hda-duplex"
            if not re.search(pattern, cmdline):
                test.fail("Can not find the %s codec for sound dev "
                          "in qemu cmd line." % codec_type)

    def check_device_address_slot(sound_device, expected_value):
        """
        Checks if the address slot of a given sound_device has expected value.

        :param sound_device: XML device object, the sound device to check
        :param expected_value: String, value the device addr. slot should match
        """
        slot_value = sound_device.address["slot"]
        if slot_value != expected_value:
            test.fail(f"Expected address slot value '{expected_value}' but got"
                      "'{slot_value}'")
        else:
            logging.info(f"Address slot value matches '{expected_value}'")

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)

    status_error = params.get("status_error", "no") == "yes"
    sound_model = params.get("sound_model")
    expected_address_slot_value = params.get("slot_value")

    # AC97 sound model supported since 0.6.0
    if sound_model == "ac97":
        if not libvirt_version.version_compare(0, 6, 0):
            test.cancel("ac97 sound model is not supported "
                        "on current version.")
    # Ich6 sound model supported since 0.8.8
    if sound_model == "ich6":
        if not libvirt_version.version_compare(0, 8, 8):
            test.cancel("ich6 sound model is not supported "
                        "on current version.")
    # Ich9 sound model supported since 1.1.3
    if sound_model == "ich9":
        if not libvirt_version.version_compare(1, 1, 3):
            test.cancel("ich9 sound model is not supported "
                        "on current version.")

    vm_xml = VMXML.new_from_dumpxml(vm_name)
    vm_xml_backup = vm_xml.copy()
    logging.debug("vm xml is %s", vm_xml_backup)

    if vm.is_alive():
        vm.destroy()

    try:
        vm_xml.remove_all_device_by_type('sound')
        sound_dev = Sound()
        sound_dev.model_type = sound_model
        if codec_type:
            sound_dev.codec_type = codec_type
        vm_xml.add_device(sound_dev)
        vm_xml.sync()
        virsh.start(vm_name, ignore_status=False)
        check_dumpxml()
        check_qemu_cmd_line()
    finally:
        if vm.is_alive():
            virsh.destroy(vm_name)
        vm_xml_backup.sync()
