import logging
from autotest.client.shared import error
from autotest.client import utils
from virttest import qemu_storage
from virttest import data_dir
from virttest import utils_selinux
from virttest import virt_vm
from virttest import virsh
from virttest import libvirt_storage
from virttest import libvirt_xml
from virttest import utils_config
from virttest import utils_libvirtd
from virttest.utils_test import libvirt as utlv
from virttest.libvirt_xml.vm_xml import VMXML


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
    # Get variables about pool vol
    with_pool_vol = 'yes' == params.get("with_pool_vol", "no")
    check_cap_rawio = "yes" == params.get("check_cap_rawio", "no")
    virt_use_nfs = params.get("virt_use_nfs", "off")
    pool_name = params.get("pool_name")
    pool_type = params.get("pool_type")
    pool_target = params.get("pool_target")
    emulated_image = params.get("emulated_image")
    vol_name = params.get("vol_name")
    vol_format = params.get("vol_format")
    # Get variables about VM and get a VM object and VMXML instance.
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vmxml = VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()
    # Get varialbles about image.
    img_label = params.get('svirt_attach_disk_disk_label')
    img_name = "svirt_disk"
    # Default label for the other disks.
    # To ensure VM is able to access other disks.
    default_label = params.get('svirt_attach_disk_disk_default_label', None)

    # Set selinux of host.
    backup_sestatus = utils_selinux.get_status()
    utils_selinux.set_status(host_sestatus)
    # Set the default label to other disks of vm.
    disks = vm.get_disk_devices()
    for disk in disks.values():
        utils_selinux.set_context_of_file(filename=disk['source'],
                                          context=default_label)

    pvt = None
    qemu_conf = utils_config.LibvirtQemuConfig()
    libvirtd = utils_libvirtd.Libvirtd()
    try:
        # set qemu conf
        if check_cap_rawio:
            qemu_conf.user = 'root'
            qemu_conf.group = 'root'
            logging.debug("the qemu.conf content is: %s" % qemu_conf)
            libvirtd.restart()

        # Set the context of the VM.
        vmxml.set_seclabel([sec_dict])
        vmxml.sync()
        logging.debug("the domain xml is: %s" % vmxml.xmltreefile)

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
                vols = pv.list_volumes().keys()
                if vols:
                    vol_name = vols[0]
                else:
                    raise error.TestNAError("No volume in pool: %s", pool_name)
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
                    raise error.TestNAError("Failed to create attach volume.")

            cmd_result = virsh.vol_path(vol_name, pool_name, debug=True)
            if cmd_result.exit_status:
                raise error.TestNAError("Failed to get volume path from pool.")
            img_path = cmd_result.stdout.strip()

            if pool_type in ["iscsi", "disk"]:
                extra = "--driver qemu --type lun --rawio --persistent"
            else:
                extra = "--persistent --subdriver qcow2"

            # set host_sestatus as nfs pool will reset it
            utils_selinux.set_status(host_sestatus)
            # set virt_use_nfs
            result = utils.run("setsebool virt_use_nfs %s" % virt_use_nfs)
            if result.exit_status:
                raise error.TestNAError("Failed to set virt_use_nfs value")
        else:
            # Init a QemuImg instance.
            params['image_name'] = img_name
            tmp_dir = data_dir.get_tmp_dir()
            image = qemu_storage.QemuImg(params, tmp_dir, img_name)
            # Create a image.
            img_path, result = image.create(params)
            # Set the context of the image.
            utils_selinux.set_context_of_file(filename=img_path, context=img_label)
            extra = "--persistent"

        # Do the attach action.
        result = virsh.attach_disk(vm_name, source=img_path, target="vdf",
                                   extra=extra, debug=True)
        if result.exit_status:
            raise error.TestFail("Failed to attach disk %s to VM."
                                 "Detail: %s." % (img_path, result.stderr))

        # Start VM to check the VM is able to access the image or not.
        try:
            vm.start()
            # Start VM successfully.
            # VM with set seclabel can access the image with the
            # set context.
            if status_error:
                raise error.TestFail('Test successed in negative case.')

            if check_cap_rawio:
                cap_list = ['CapPrm', 'CapEff', 'CapBnd']
                cap_dict = {}
                pid = vm.get_pid()
                pid_status_path = "/proc/%s/status" % pid
                with open(pid_status_path) as f:
                    for line in f:
                        val_list = line.split(":")
                        if val_list[0] in cap_list:
                            cap_dict[val_list[0]] = val_list[1].strip()

                # use capsh to check cap_sys_rawio is set
                for i in cap_list:
                    cmd = "capsh --decode=%s" % cap_dict[i]
                    result = utils.run(cmd)
                    if result.exit_status:
                        raise error.TestNAError("Failed to run capsh command")
                    else:
                        if "cap_sys_rawio" not in result.stdout:
                            err_msg = "vm process with %s:%s" % (i, cap_dict[i])
                            err_msg += " lack cap_sys_rawio capabilities"
                            raise error.TestFail(err_msg)
                        else:
                            inf_msg = "vm process with %s:%s" % (i, cap_dict[i])
                            inf_msg += " have cap_sys_rawio capabilities"
                            logging.debug(inf_msg)

        except virt_vm.VMStartError, e:
            # Starting VM failed.
            # VM with set seclabel can not access the image with the
            # set context.
            if not status_error:
                raise error.TestFail("Test failed in positive case."
                                     "error: %s" % e)

        try:
            virsh.detach_disk(vm_name, target="vdf", extra="--persistent",
                              debug=True)
        except error.CmdError:
            raise error.TestFail("Detach disk 'vdf' from VM %s failed."
                                 % vm.name)
    finally:
        # clean up
        vm.destroy()
        if not with_pool_vol:
            image.remove()
        if pvt:
            try:
                pvt.cleanup_pool(pool_name, pool_type, pool_target,
                                 emulated_image)
            except error.TestFail, detail:
                logging.error(str(detail))
        backup_xml.sync()
        utils_selinux.set_status(backup_sestatus)
        if check_cap_rawio:
            qemu_conf.restore()
            libvirtd.restart()
