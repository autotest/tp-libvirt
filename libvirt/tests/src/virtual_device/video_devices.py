import logging
import re

from virttest.libvirt_xml.vm_xml import VMXML
from virttest.libvirt_xml.devices.video import Video
from virttest import virsh
from virttest import libvirt_version

from six import iteritems

from math import ceil
from math import log


def run(test, params, env):
    """
    Test the video virtual devices

    1. prepare a guest with different video devices
    2. check whether the guest can be started, and set
       the related params
    3. check the qemu cmd line and the params
    """
    def check_heads_test_xml(model_type, is_primary=None, **kwargs):
        """
        Check whether the added devices and attributes are shown
        in the guest xml
        """
        if is_primary or is_primary is None:
            model_heads = kwargs.get("model_heads", default_primary_heads)
        else:
            model_heads = kwargs.get("model_heads", default_secondary_heads)
        pattern_model_type = "type=\'%s\'" % model_type
        pattern_heads = "heads=\'%s\'" % model_heads
        xml_after_adding_device = str(VMXML.new_from_dumpxml(vm_name))

        for line in xml_after_adding_device.splitlines():
            if pattern_model_type in line:
                if pattern_heads not in line:
                    test.fail("Can not find %s video device or heads num"
                              "is not as settings in the xml" % model_type)

    def check_mem_test_xml(model_type, mem_type, mem_size):
        """
        Check whether the added devices and attributes are shown
        in the guest xml
        """
        pattern_model_type = "type=\'%s\'" % model_type
        pattern_mem = "%s=\'%s\'" % (mem_type, mem_size)
        xml_after_adding_device = str(VMXML.new_from_dumpxml(vm_name))

        for line in xml_after_adding_device.splitlines():
            if pattern_model_type in line:
                if pattern_mem not in line:
                    test.fail("Can not find %s video device or memory mem_size"
                              "for %s is not as settings in the xml"
                              % (model_type, mem_type))

    def add_video_device(video_model, domain_xml, is_primary=None,
                         status_error=False, **kwargs):
        """
        add the video device xml snippet, then sync the guest xml
        """
        video_dev = Video()
        video_dev.model_type = video_model
        if is_primary:
            video_dev.primary = "yes"

        for key, value in list(iteritems(kwargs)):
            setattr(video_dev, key, value)
        domain_xml.add_device(video_dev)
        try:
            domain_xml.sync()
        except Exception as error:
            if not status_error:
                test.fail("Failed to define the guest after adding the %s video "
                          "device xml. Details: %s " % (video_model, error))
            logging.debug("This is the expected failing in negative cases.")
        else:
            if status_error:
                test.fail("xml sync should failed as it is a negative case.")
            logging.debug("Add devices succeed in postive case.")

    def check_model_test_cmd_line(model_type, is_primary=None):
        """
        Check whether the added video devices are shown in the qemu cmd line
        """
        cmdline = open('/proc/%s/cmdline' % vm.get_pid()).read().replace("\x00", " ")
        logging.debug("the cmdline is: %s" % cmdline)
        # s390x only supports virtio
        s390x_pattern = r"-device\svirtio-gpu-ccw"
        # aarch64 only supports virtio
        aarch64_pattern = r"-device\svirtio-gpu-pci"

        if is_primary or is_primary is None:
            if model_type == "vga":
                pattern = r"-device\sVGA"
            else:
                pattern = r"-device\s%s-vga" % model_type
            if guest_arch == 's390x':
                pattern = s390x_pattern
            elif guest_arch == 'aarch64':
                pattern = aarch64_pattern
            if not re.search(pattern, cmdline):
                test.fail("Can not find the primary %s video device "
                          "in qemu cmd line." % model_type)
        else:
            if model_type == "qxl":
                pattern = r"-device\sqxl,"
            elif model_type == "virtio":
                pattern = r"-device\svirtio-gpu-pci"
                if with_packed:
                    pattern = r"-device\svirtio-gpu-pci.*packed=%s" % driver_packed
            if guest_arch == 's390x':
                pattern = s390x_pattern
            if not re.search(pattern, cmdline):
                test.fail("Can not find the secondary %s video device "
                          "in qemu cmd line." % model_type)

    def check_heads_test_cmd_line(model_type, is_primary=None, **kwargs):
        """
        Check whether the heads number of video devices in the qemu cmd line
        are just the same with settings.
        """
        cmdline = open('/proc/%s/cmdline' % vm.get_pid()).read().replace("\x00", " ")
        logging.debug("the cmdline is: %s" % cmdline)
        # s390x only supports virtio
        s390x_pattern = r"-device\svirtio-gpu-ccw\S+max_outputs=%s"
        # aarch64 only supports virtio
        aarch64_pattern = r"-device\svirtio-gpu-pci\S+max_outputs=%s"

        if is_primary or is_primary is None:
            model_heads = kwargs.get("model_heads", default_primary_heads)
            if model_type == "qxl" or model_type == "virtio":
                pattern = r"-device\s%s-vga\S+max_outputs=%s" % (model_type, model_heads)
                if guest_arch == 's390x':
                    pattern = s390x_pattern % model_heads
                elif guest_arch == 'aarch64':
                    pattern = aarch64_pattern % model_heads
                if not re.search(pattern, cmdline):
                    test.fail("The heads number of the primary %s video device "
                              "in not correct." % model_type)
        else:
            model_heads = kwargs.get("model_heads", default_secondary_heads)
            if model_type == "qxl":
                pattern = r"-device\sqxl\S+max_outputs=%s" % model_heads
            elif model_type == "virtio":
                pattern = r"-device\svirtio-gpu-pci\S+max_outputs=%s" % model_heads
            if guest_arch == 's390x':
                pattern = s390x_pattern % model_heads
            if not re.search(pattern, cmdline):
                test.fail("The heads number of the secondary %s video device "
                          "in not correct." % model_type)

    def check_mem_test_cmd_line(model_type, mem_type, mem_size):
        """
        Check whether the video memory of video devices in the qemu cmd line
        are just the same with settings.
        """
        cmdline = open('/proc/%s/cmdline' % vm.get_pid()).read().replace("\x00", " ")
        logging.debug("the cmdline is: %s" % cmdline)

        if mem_type == "ram" or mem_type == "vram":
            cmd_mem_size = str(int(mem_size)*1024)
            pattern = r"-device\sqxl-vga\S+%s_size=%s" % (mem_type, cmd_mem_size)
        if mem_type == "vram" and model_type == "vga":
            cmd_mem_size = str(int(mem_size)//1024)
            pattern = r"-device\sVGA\S+vgamem_mb=%s" % cmd_mem_size
        if mem_type == "vgamem":
            cmd_mem_size = str(int(mem_size)//1024)
            pattern = r"-device\sqxl-vga\S+vgamem_mb=%s" % cmd_mem_size
        if mem_type == "vram64":
            cmd_mem_size = str(int(mem_size)//1024)
            pattern = r"-device\sqxl-vga\S+vram64_size_mb=%s" % cmd_mem_size

        if not re.search(pattern, cmdline):
            test.fail("The %s memory size of %s video device "
                      "in not correct." % (mem_type, model_type))

    def up_round_to_power_of_two(num):
        power = ceil(log(int(num), 2))
        return pow(2, power)

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    status_error = params.get("status_error", "no") == "yes"
    model_test = params.get("model_test", "no") == "yes"
    primary_video_model = params.get("primary_video_model")
    secondary_video_model = params.get("secondary_video_model", None)
    heads_test = params.get("heads_test", "no") == "yes"
    default_primary_heads = params.get("default_primary_heads", None)
    default_secondary_heads = params.get("default_secondary_heads", None)
    primary_heads = params.get("primary_heads", None)
    secondary_heads = params.get("secondary_heads", None)
    mem_test = params.get("mem_test", "no") == "yes"
    mem_type = params.get("mem_type", None)
    mem_size = params.get("mem_size", None)
    default_mem_size = params.get("default_mem_size", None)
    zero_size_test = params.get("zero_size_test", None) == "yes"
    non_power_of_2_test = params.get("non_power_of_2_test", None) == "yes"
    guest_arch = params.get("vm_arch_name")
    with_packed = params.get("with_packed", "no") == "yes"
    driver_packed = params.get("driver_packed", "on")

    vm_xml = VMXML.new_from_dumpxml(vm_name)
    vm_xml_backup = vm_xml.copy()
    if vm.is_alive():
        vm.destroy()

    if with_packed and not libvirt_version.version_compare(6, 3, 0):
        test.cancel("The virtio packed attribute is not supported in"
                    " current libvirt version.")

    try:
        vm_xml.remove_all_device_by_type('video')
        kwargs = {}
        model_type = primary_video_model
        is_primary = None
        if secondary_video_model:
            is_primary = True
        if heads_test and not default_primary_heads:
            kwargs["model_heads"] = primary_heads
        if mem_test and not default_mem_size:
            kwargs["model_"+mem_type] = mem_size
        if model_type == "virtio" and with_packed:
            kwargs["driver_packed"] = driver_packed
        add_video_device(model_type, vm_xml, is_primary, status_error, **kwargs)

        if secondary_video_model:
            kwargs = {}
            model_type = secondary_video_model
            is_primary = False
            if heads_test and not default_secondary_heads:
                kwargs["model_heads"] = secondary_heads
            if model_type == "virtio" and with_packed:
                kwargs["driver_packed"] = driver_packed
            add_video_device(model_type, vm_xml, is_primary, status_error, **kwargs)

        if not status_error:
            res = virsh.start(vm_name)

            if res.exit_status:
                test.fail("failed to start vm after adding the video "
                          "device xml. details: %s " % res)
            logging.debug("vm started successfully in postive cases.")

            if model_test:
                check_model_test_cmd_line(model_type, is_primary)

            if heads_test:
                check_heads_test_xml(model_type, is_primary, **kwargs)
                check_heads_test_cmd_line(model_type, is_primary, **kwargs)

            if mem_test:
                if mem_size is None:
                    mem_size = default_mem_size
                if zero_size_test:
                    mem_size = params.get("mem_size_after_define")
                if non_power_of_2_test:
                    mem_size = up_round_to_power_of_two(mem_size)

                check_mem_test_xml(model_type, mem_type, mem_size)
                check_mem_test_cmd_line(model_type, mem_type, mem_size)
    finally:
        if vm.is_alive():
            vm.destroy(vm_name)
        vm_xml_backup.sync()
