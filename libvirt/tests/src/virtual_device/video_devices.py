import logging
import re

from virttest.libvirt_xml.vm_xml import VMXML
from virttest.libvirt_xml.devices.video import Video
from virttest import virsh

#from provider import libvirt_version
from six import iteritems


def run(test, params, env):
    """
    Test the video virtual devices

    1. prepare a guest with different video devices
    2. check whether the guest can be started, and set
       the related params
    3. check the qemu cmd line and the params
    """
    def add_video_device(video_model, domain_xml, is_primary,
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

    def check_model_test_cmd_line(model_type, is_primary):
        """
        Check whether the added video devices are shown in the qemu cmd line
        """
        cmdline = open('/proc/%s/cmdline' % vm.get_pid()).read().replace("\x00", " ")
        logging.debug("the cmdline is: %s" % cmdline)

        if is_primary:
            if model_type == "vga":
                pattern = r"-device\sVGA"
            else:
                pattern = r"-device\s%s-vga" % model_type
            logging.debug("the pattern is : %s" % pattern)
            if not re.search(pattern, cmdline):
                test.fail("Can not find the primary %s video device "
                          "in qemu cmd line." % model_type)
        else:
            if model_type == "qxl":
                pattern = r"-device\sqxl,"
            elif model_type == "virtio":
                pattern = r"-device\svirtio-gpu-pci"
            logging.debug("the pattern is : %s" % pattern)
            if not re.search(pattern, cmdline):
                test.fail("Can not find the secondary %s video device "
                          "in qemu cmd line." % model_type)

    def check_heads_test_cmd_line(model_type, is_primary, **kwargs):
        """
        Check whether the heads number of video devices in the qemu cmd line
        are just the same with settings.
        """
        cmdline = open('/proc/%s/cmdline' % vm.get_pid()).read().replace("\x00", " ")
        logging.debug("the cmdline is: %s" % cmdline)
        if is_primary:
            model_heads = kwargs.get("model_heads", default_primary_heads)
            logging.debug("model_heads is %s" % model_heads)
            pattern = r"-device\s%s-vga\S+max_outputs=%s" % (model_type, model_heads)
            logging.debug("the pattern is : %s" % pattern)
            if not re.search(pattern, cmdline):
                test.fail("The heads number of the primary %s video device "
                          "in not correct." % model_type)
        else:
            model_heads = kwargs.get("model_heads", default_secondary_heads)
            if model_type == "qxl":
                pattern = r"-device\sqxl\S+max_outputs=%s" % model_heads
            elif model_type == "virtio":
                pattern = r"-device\svirtio-gpu-pci\S+max_outputs=%s" % model_heads
            logging.debug("the pattern is : %s" % pattern)
            if not re.search(pattern, cmdline):
                test.fail("The heads number of the secondary %s video device "
                          "in not correct." % model_type)

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
    mem_type = params.get("mem_type", None)

    vm_xml = VMXML.new_from_dumpxml(vm_name)
    vm_xml_backup = vm_xml.copy()
    if vm.is_alive():
        vm.destroy()

    try:
        vm_xml.remove_all_device_by_type('video')
        kwargs = {}
        logging.debug("primary_heads is %s " % primary_heads)
        model_type = primary_video_model
        is_primary = True
        if heads_test and not default_primary_heads:
            kwargs["model_heads"] = primary_heads
        add_video_device(model_type, vm_xml, is_primary, **kwargs)
        if secondary_video_model:
            model_type = secondary_video_model
            is_primary = False
            if heads_test and not default_secondary_heads:
                kwargs["model_heads"] = secondary_heads
            add_video_device(model_type, vm_xml, is_primary, **kwargs)
        logging.debug("vm_xml is %s" % vm_xml)

        res = virsh.start(vm_name)
        if res.exit_status:
            if not status_error:
                test.fail("failed to start vm after adding the video "
                          "device xml. details: %s " % res)
            logging.debug("this is the expected failure in negative cases.")
            return
        if status_error:
            test.fail("expected fail in negative cases but vm started successfully.")
            return

        logging.debug("vm started successfully in postive cases.")

        if model_test:
            check_model_test_cmd_line(model_type, is_primary)

        if heads_test:
            check_heads_test_cmd_line(model_type, is_primary, **kwargs)

    finally:
        if vm.is_alive():
            vm.destroy(vm_name)
        vm_xml_backup.sync()
