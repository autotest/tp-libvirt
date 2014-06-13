import os
import logging
from autotest.client import utils
from autotest.client.shared import error
from virttest import aexpect, virt_vm, virsh, remote, qemu_storage
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.disk import Disk
from virttest.libvirt_xml.devices.controller import Controller


def run(test, params, env):
    """
    Test disk attachement of multiple disks.

    1.Prepare test environment, destroy VMs.
    2.Perform 'qemu-img create' operation.
    3.Edit disks xml and start the domains.
    4.Perform test operation.
    5.Recover test environment.
    6.Confirm the test result.
    """

    def get_scsi_disk(scsi_option):
        """
        Get the scsi device created by scsi_debug kernel module

        :param scsi_option. The scsi_debug kernel module options.
        :return: scsi device if it is created successfully.
        """
        try:
            # Load scsi_debug kernel module.
            # Unload it first if it's already loaded.
            if utils.module_is_loaded("scsi_debug"):
                utils.unload_module("scsi_debug")
            utils.load_module("scsi_debug dev_size_mb=1024 %s" % scsi_option)
            # Get the scsi device name
            scsi_disk = utils.run("lsscsi|grep scsi_debug|"
                                  "awk '{print $6}'").stdout.strip()
            logging.info("scsi disk: %s" % scsi_disk)
            return scsi_disk
        except Exception, e:
            logging.error(repr(e))
            return None

    def create_disk_img(name, path, fmt):
        """
        Create disk image.

        :param name. Image file name.
        :param path. Image file path.
        :param fmt. Image file format.
        :return: QemuImg object and image file name
        """
        try:
            params["image_name"] = name
            params["image_format"] = fmt
            params["image_size"] = "100M"
            img_dev = qemu_storage.QemuImg(params, path, "")
            disk_source, _ = img_dev.create(params)
            logging.debug("disk source: %s", disk_source)
            utils.run("mkfs.ext3 -F %s" % disk_source)
            return img_dev, disk_source
        except Exception, e:
            logging.error(repr(e))
            raise error.TestNAError("Test skipped because of creating"
                                    " disk image failed")

    def set_vm_controller_xml(vmxml):
        """
        Set VM scsi controller xml.

        :param vmxml. Domain xml object.
        """
        # Add disk scsi controller
        scsi_controller = Controller("controller")
        scsi_controller.type = "scsi"
        scsi_controller.index = "0"
        scsi_controller.model = "virtio-scsi"
        vmxml.add_device(scsi_controller)

        # Redefine domain
        vmxml.sync()

    def get_vm_disk_xml(dev_type, dev_name, sgio="", share="", options=""):
        """
        Create a disk xml object and return it.

        :param dev_type. Disk type.
        :param dev_name. Disk device name.
        :param sgio. Disk sgio option.
        :param share. Disk shareable option.
        :return: Disk xml object.
        """
        # Create disk xml
        disk_xml = Disk(type_name=dev_type)
        if sgio != "":
            disk_xml.sgio = sgio
            disk_xml.device = "lun"
            disk_xml.rawio = "no"
            disk_attr = "dev"
            disk_xml.target = {'dev': 'sda', 'bus': 'scsi'}
        else:
            disk_xml.device = "disk"
            disk_attr = "file"
            disk_xml.target = {'dev': 'vdb', 'bus': 'virtio'}
        disk_xml.source = disk_xml.new_disk_source(
            **{'attrs': {disk_attr: dev_name}})

        # Add driver options from parameters.
        driver_dict = {"name": "qemu", "type": "raw"}
        if options != "":
            for driver_option in options.split(','):
                if driver_option != "":
                    d = driver_option.split('=')
                    logging.debug("disk driver option: %s=%s", d[0], d[1])
                    driver_dict.update({d[0].strip(): d[1].strip()})

        disk_xml.driver = driver_dict
        if share == "shareable":
            disk_xml.share = True

        return disk_xml

    vm_names = params.get("vms").split()
    if len(vm_names) < 2:
        raise error.TestNAError("No multi vms provided.")

    # Disk specific attributes.
    vms_sgio = params.get("virt_disk_vms_sgio", "").split()
    vms_share = params.get("virt_disk_vms_share", "").split()
    disk_bus = params.get("virt_disk_bus", "virtio")
    disk_target = params.get("virt_disk_target", "vdb")
    disk_type = params.get("virt_disk_tyep", "file")
    scsi_options = params.get("scsi_options", "")
    disk_driver_options = params.get("disk_driver_options", "")
    hotplug = "yes" == params.get("virt_disk_vms_hotplug", "no")
    status_error = params.get("status_error").split()
    test_error_policy = "yes" == params.get("virt_disk_test_error_policy", "no")
    disk_source_path = test.virtdir

    disks = []
    if disk_bus == "scsi":
        disk_source = get_scsi_disk(scsi_options)
        if not disk_source:
            raise error.TestNAError("Get scsi disk failed.")
        disks.append({"format": "scsi", "source": disk_source})

    elif disk_bus == "virtio":
        disk_dev, disk_source = create_disk_img("test", disk_source_path, "raw")
        disks.append({"format": "raw", "disk_dev": disk_dev, "source": disk_source})

    # Backup xml files, compose the new domain xml
    vms_list = []
    for i in range(len(vm_names)):
        vm_xml_file = os.path.join(test.tmpdir,
                                   vm_names[i] + "_backup_.xml")
        virsh.dumpxml(vm_names[i], extra="--inactive",
                      to_file=vm_xml_file)
        vm = env.get_vm(vm_names[i])
        # Destroy domain first.
        if vm.is_alive():
            vm.destroy(gracefully=False)

        # Configure vm disk options and define vm
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_names[i])
        if disk_bus == "scsi":
            set_vm_controller_xml(vmxml)
        disk_sgio = ""
        if len(vms_sgio) > i:
            sgio = vms_sgio[i]
        disk_sharable = ""
        if len(vms_share) > i:
            share = vms_share[i]
        disk_xml = get_vm_disk_xml(disk_type, disk_source,
                                   disk_sgio, disk_sharable,
                                   disk_driver_options)
        if not hotplug:
            # If we are not testing hotplug,
            # add disks to domain xml and sync.
            vmxml.add_device(disk_xml)
            vmxml.sync()
        vms_list.append({"name": vm_names[i], "vm": vm,
                         "backup": vm_xml_file,
                         "status": "yes" == status_error[i],
                         "disk": disk_xml})
        logging.debug("vms_list %s" % vms_list)

    try:
        for i in range(len(vms_list)):
            try:
                # Try to start the domain.
                vms_list[i]['vm'].start()
                session = vms_list[i]['vm'].wait_for_login()
                # if we are testing hotplug, it need to start domain and
                # then run virsh attach-device command.
                if hotplug:
                    vms_list[i]['disk'].xmltreefile.write()
                    result = virsh.attach_device(vms_list[i]['name'],
                                                 vms_list[i]['disk'].xml).exit_status
                    os.remove(vms_list[i]['disk'].xml)

                    # Check if the return code of attach-device
                    # command is as expected.
                    if 0 != result and vms_list[i]['status']:
                        raise error.TestFail('Failed to hotplug disk device')
                    elif 0 == result and not vms_list[i]['status']:
                        raise error.TestFail('Hotplug disk device unexpectedly.')
                # Check if VM is started as expected.
                if not vms_list[i]['status']:
                    raise error.TestFail('VM started unexpectedly.')

                # Check disk error_policy option in VM.
                if test_error_policy:
                    error_policy = vms_list[i]['disk'].driver["error_policy"]
                    if i == 0:
                        # If we testing enospace error policy, only 1 vm used
                        if error_policy == "enospace":
                            cmd = ("mount /dev/%s /mnt && dd if=/dev/zero of=/mnt/test"
                                   " bs=1M count=200 2>&1 | grep 'No space left'"
                                   % disk_target)
                            s, o = session.cmd_status_output(cmd)
                            logging.debug("error_policy in vm0 exit %s; output: %s", s, o)
                            if 0 != s:
                                raise error.TestFail("Test error_policy %s: cann't see"
                                                     " error messages")
                            session.close()
                            break

                        if session.cmd_status("fdisk -l /dev/%s && mount /dev/%s /mnt; ls /mnt"
                                              % (disk_target, disk_target)):
                            session.close()
                            raise error.TestFail("Test error_policy: "
                                                 "failed to mount disk")
                        session.close()
                    if i == 1:
                        try:
                            session0 = vms_list[0]['vm'].wait_for_login(timeout=10)
                            cmd = ("fdisk -l /dev/%s && mkfs.ext3 -F /dev/%s "
                                   % (disk_target, disk_target))
                            s, o = session.cmd_status_output(cmd)
                            logging.debug("error_policy in vm1 exit %s; output: %s", s, o)
                            session.close()
                            cmd = ("dd if=/dev/zero of=/mnt/test bs=1M count=50 && dd if="
                                   "/mnt/test of=/dev/null bs=1M; dmesg | grep 'I/O error'")
                            s, o = session0.cmd_status_output(cmd)
                            logging.debug("session in vm0 exit %s; output: %s", s, o)
                            if error_policy == "report":
                                if 0 != s:
                                    raise error.TestFail("Test error_policy %s: cann't report"
                                                         " error" % error_policy)
                            elif error_policy == "ignore":
                                if 0 == s:
                                    raise error.TestFail("Test error_policy %s: error cann't"
                                                         " be ignored" % error_policy)
                            session0.close()
                        except (remote.LoginError, virt_vm.VMError, aexpect.ShellError), e:
                            if error_policy == "stop":
                                if not vms_list[0]['vm'].is_paused():
                                    raise error.TestFail("Test error_policy %s: cann't stop"
                                                         " VM" % error_policy)
                            else:
                                logging.error(str(e))
                                raise error.TestFail("Test error_policy %s: login failed"
                                                     % error_policy)

            except virt_vm.VMStartError:
                if vms_list[i]['status']:
                    raise error.TestFail('VM Failed to start'
                                         ' for some reason!')
    finally:
        # Recover VMs.
        for i in range(len(vm_names)):
            if vms_list[i]['vm'].is_alive():
                vms_list[i]['vm'].destroy(gracefully=False)
            logging.info("Restoring vm...")
            virsh.undefine(vms_list[i]['name'])
            virsh.define(vms_list[i]['backup'])

        # Remove disks.
        for img in disks:
            if img.has_key("disk_dev"):
                img["disk_dev"].remove()
            else:
                if img["format"] == "scsi":
                    utils.unload_module("scsi_debug")
