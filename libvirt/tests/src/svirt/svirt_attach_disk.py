import logging

from avocado.utils import process
from avocado.utils import astring
from avocado.core import exceptions

from virttest import qemu_storage
from virttest import data_dir
from virttest import utils_selinux
from virttest import virt_vm
from virttest import virsh
from virttest import libvirt_storage
from virttest import libvirt_xml
from virttest import utils_config
from virttest import utils_libvirtd
from virttest import libvirt_version
from virttest.utils_test import libvirt as utlv
from virttest.utils_test import libvirt
from virttest.libvirt_xml.vm_xml import VMXML
from virttest.libvirt_xml.devices.disk import Disk
from virttest.libvirt_xml.devices import seclabel
from virttest.libvirt_xml.devices.controller import Controller


def run(test, params, env):
    """
    Test svirt in adding disk to VM.

    (1).Init variables for test.
    (2).Create a image to attached to VM.
    (3).Attach disk.
    (4).Start VM and check result.
    """
    # Get general variables.
    status_error = ('yes' == params.get("status_error", 'no'))
    host_sestatus = params.get("svirt_attach_disk_host_selinux", "enforcing")
    # Get variables about seclabel for VM.
    sec_type = params.get("svirt_attach_disk_vm_sec_type", "dynamic")
    sec_model = params.get("svirt_attach_disk_vm_sec_model", "selinux")
    sec_label = params.get("svirt_attach_disk_vm_sec_label", None)
    sec_relabel = params.get("svirt_attach_disk_vm_sec_relabel", "yes")
    sec_dict = {'type': sec_type, 'model': sec_model, 'label': sec_label,
                'relabel': sec_relabel}
    disk_seclabel = params.get("disk_seclabel", "no")
    # Get variables about pool vol
    with_pool_vol = 'yes' == params.get("with_pool_vol", "no")
    check_cap_rawio = "yes" == params.get("check_cap_rawio", "no")
    virt_use_nfs = params.get("virt_use_nfs", "off")
    pool_name = params.get("pool_name")
    pool_type = params.get("pool_type")
    pool_target = params.get("pool_target")
    emulated_image = params.get("emulated_image")
    vol_name = params.get("vol_name")
    vol_format = params.get("vol_format", "qcow2")
    device_target = params.get("disk_target")
    device_bus = params.get("disk_target_bus")
    device_type = params.get("device_type", "file")
    # Get variables about VM and get a VM object and VMXML instance.
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vmxml = VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()
    # Get varialbles about image.
    img_label = params.get('svirt_attach_disk_disk_label')
    sec_disk_dict = {'model': sec_model, 'label': img_label, 'relabel': sec_relabel}
    enable_namespace = 'yes' == params.get('enable_namespace', 'no')
    img_name = "svirt_disk"
    # Default label for the other disks.
    # To ensure VM is able to access other disks.
    default_label = params.get('svirt_attach_disk_disk_default_label', None)

    # Set selinux of host.
    backup_sestatus = utils_selinux.get_status()
    utils_selinux.set_status(host_sestatus)
    # Set the default label to other disks of vm.
    disks = vm.get_disk_devices()
    for disk in list(disks.values()):
        utils_selinux.set_context_of_file(filename=disk['source'],
                                          context=default_label)

    pvt = None
    qemu_conf = utils_config.LibvirtQemuConfig()
    libvirtd = utils_libvirtd.Libvirtd()
    disk_xml = Disk(type_name=device_type)
    disk_xml.device = "disk"
    try:
        # set qemu conf
        if check_cap_rawio:
            qemu_conf.user = 'root'
            qemu_conf.group = 'root'
            logging.debug("the qemu.conf content is: %s" % qemu_conf)
            libvirtd.restart()

        if with_pool_vol:
            # Create dst pool for create attach vol img
            pvt = utlv.PoolVolumeTest(test, params)
            logging.debug("pool_type %s" % pool_type)
            pvt.pre_pool(pool_name, pool_type, pool_target,
                         emulated_image, image_size="1G",
                         pre_disk_vol=["20M"])

            if pool_type in ["iscsi", "disk"]:
                # iscsi and disk pool did not support create volume in libvirt,
                # logical pool could use libvirt to create volume but volume
                # format is not supported and will be 'raw' as default.
                pv = libvirt_storage.PoolVolume(pool_name)
                vols = list(pv.list_volumes().keys())
                vol_format = "raw"
                if vols:
                    vol_name = vols[0]
                else:
                    test.cancel("No volume in pool: %s" % pool_name)
            else:
                vol_arg = {'name': vol_name, 'format': vol_format,
                           'capacity': 1073741824,
                           'allocation': 1048576, }
                # Set volume xml file
                volxml = libvirt_xml.VolXML()
                newvol = volxml.new_vol(**vol_arg)
                vol_xml = newvol['xml']

                # Run virsh_vol_create to create vol
                logging.debug("create volume from xml: %s" % newvol.xmltreefile)
                cmd_result = virsh.vol_create(pool_name, vol_xml,
                                              ignore_status=True,
                                              debug=True)
                if cmd_result.exit_status:
                    test.cancel("Failed to create attach volume.")

            cmd_result = virsh.vol_path(vol_name, pool_name, debug=True)
            if cmd_result.exit_status:
                test.cancel("Failed to get volume path from pool.")
            img_path = cmd_result.stdout.strip()

            if pool_type in ["iscsi", "disk"]:
                source_type = "dev"
                if pool_type == "iscsi":
                    disk_xml.device = "lun"
                    disk_xml.rawio = "yes"
                else:
                    if not enable_namespace:
                        qemu_conf.namespaces = ''
                        logging.debug("the qemu.conf content is: %s" % qemu_conf)
                        libvirtd.restart()
            else:
                source_type = "file"

            # set host_sestatus as nfs pool will reset it
            utils_selinux.set_status(host_sestatus)
            # set virt_use_nfs
            result = process.run("setsebool virt_use_nfs %s" % virt_use_nfs,
                                 shell=True)
            if result.exit_status:
                test.cancel("Failed to set virt_use_nfs value")
        else:
            source_type = "file"
            # Init a QemuImg instance.
            params['image_name'] = img_name
            tmp_dir = data_dir.get_tmp_dir()
            image = qemu_storage.QemuImg(params, tmp_dir, img_name)
            # Create a image.
            img_path, result = image.create(params)
            # Set the context of the image.
            if sec_relabel == "no":
                utils_selinux.set_context_of_file(filename=img_path, context=img_label)

        disk_xml.target = {"dev": device_target, "bus": device_bus}
        disk_xml.driver = {"name": "qemu", "type": vol_format}
        if disk_seclabel == "yes":
            source_seclabel = []
            sec_xml = seclabel.Seclabel()
            sec_xml.update(sec_disk_dict)
            source_seclabel.append(sec_xml)
            disk_source = disk_xml.new_disk_source(**{"attrs": {source_type: img_path},
                                                      "seclabels": source_seclabel})
        else:
            disk_source = disk_xml.new_disk_source(**{"attrs": {source_type: img_path}})
            # Set the context of the VM.
            vmxml.set_seclabel([sec_dict])
            vmxml.sync()

        disk_xml.source = disk_source
        logging.debug(disk_xml)

        # Replace old scsi controller with virtio-scsi controller to support hotplug/unplug
        if params.get('machine_type') == 'pseries' and device_bus == 'scsi':
            if not vmxml.get_controllers(device_bus, 'virtio-scsi'):
                vmxml.del_controller(device_bus)
                ppc_controller = Controller('controller')
                ppc_controller.type = device_bus
                ppc_controller.index = '0'
                ppc_controller.model = 'virtio-scsi'
                vmxml.add_device(ppc_controller)
                vmxml.sync()

        # Do the attach action.
        cmd_result = virsh.attach_device(domainarg=vm_name, filearg=disk_xml.xml, flagstr='--persistent')
        libvirt.check_exit_status(cmd_result, expect_error=False)
        logging.debug("the domain xml is: %s" % vmxml.xmltreefile)

        # Start VM to check the VM is able to access the image or not.
        try:
            vm.start()
            # Start VM successfully.
            # VM with set seclabel can access the image with the
            # set context.
            if status_error:
                test.fail('Test succeeded in negative case.')

            if check_cap_rawio:
                cap_list = ['CapPrm', 'CapEff', 'CapBnd']
                cap_dict = {}
                pid = vm.get_pid()
                pid_status_path = "/proc/%s/status" % pid
                with open(pid_status_path) as f:
                    for line in f:
                        val_list = line.split(":")
                        if val_list[0] in cap_list:
                            cap_dict[val_list[0]] = int(val_list[1].strip(), 16)

                # bit and with rawio capabilitiy value to check cap_sys_rawio
                # is set
                cap_rawio_val = 0x0000000000020000
                for i in cap_list:
                    if not cap_rawio_val & cap_dict[i]:
                        err_msg = "vm process with %s: 0x%x" % (i, cap_dict[i])
                        err_msg += " lack cap_sys_rawio capabilities"
                        test.fail(err_msg)
                    else:
                        inf_msg = "vm process with %s: 0x%x" % (i, cap_dict[i])
                        inf_msg += " have cap_sys_rawio capabilities"
                        logging.debug(inf_msg)
            if pool_type == "disk":
                if libvirt_version.version_compare(3, 1, 0) and enable_namespace:
                    vm_pid = vm.get_pid()
                    output = process.system_output(
                        "nsenter -t %d -m -- ls -Z %s" % (vm_pid, img_path))
                else:
                    output = process.system_output('ls -Z %s' % img_path)
                logging.debug("The default label is %s", default_label)
                logging.debug("The label after guest started is %s", astring.to_text(output.strip().split()[-2]))
                if default_label not in astring.to_text(output.strip().split()[-2]):
                    test.fail("The label is wrong after guest started\n")
        except virt_vm.VMStartError as e:
            # Starting VM failed.
            # VM with set seclabel can not access the image with the
            # set context.
            if not status_error:
                test.fail("Test failed in positive case."
                          "error: %s" % e)

        cmd_result = virsh.detach_device(domainarg=vm_name, filearg=disk_xml.xml)
        libvirt.check_exit_status(cmd_result, status_error)
    finally:
        # clean up
        vm.destroy()
        if not with_pool_vol:
            image.remove()
        if pvt:
            try:
                pvt.cleanup_pool(pool_name, pool_type, pool_target,
                                 emulated_image)
            except exceptions.TestFail as detail:
                logging.error(str(detail))
        backup_xml.sync()
        utils_selinux.set_status(backup_sestatus)
        if check_cap_rawio:
            qemu_conf.restore()
            libvirtd.restart()
