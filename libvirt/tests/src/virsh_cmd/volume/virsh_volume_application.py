import os
import time
import logging

from avocado.utils import process

from virttest import libvirt_storage
from virttest import virsh
from virttest import data_dir
from virttest import utils_selinux
from virttest import utils_misc
from virttest.utils_test import libvirt as utlv
from virttest.tests import unattended_install


def create_volumes(test, new_pool, volume_count, volume_size):
    """
    Create some volumes and return them, not including
    existed volumes
    """
    count = 1
    created_volumes = {}
    while count <= volume_count:
        vol_name = "volume%s" % count
        count += 1
        # TODO: Check whether there is sufficient space.
        if not new_pool.create_volume(vol_name, volume_size):
            test.fail("Create volume %s failed." % vol_name)
        volumes = new_pool.list_volumes()
        logging.debug("Current volumes:%s", volumes)
        if vol_name in list(volumes.keys()):
            created_volumes[vol_name] = volumes[vol_name]
    return created_volumes


def run(test, params, env):
    """
    Test storage pool and volumes with applications such as:
    install vms, attached to vms...
    """
    pool_type = params.get("pool_type")
    pool_name = "test_%s_app" % pool_type
    pool_target = params.get("pool_target")
    emulated_img = params.get("emulated_image", "emulated-image")
    volume_count = int(params.get("volume_count", 1))
    volume_size = params.get("volume_size", "1G")
    emulated_size = "%sG" % (volume_count * int(volume_size[:-1]) + 1)
    application = params.get("application", "install")
    disk_target = params.get("disk_target", "vdb")
    test_message = params.get("test_message", "")
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    block_device = params.get("block_device", "/DEV/EXAMPLE")
    if application == "install":
        cdrom_path = os.path.join(data_dir.get_data_dir(),
                                  params.get("cdrom_cd1"))
        if not os.path.exists(cdrom_path):
            test.cancel("Can't find installation cdrom:%s"
                        % cdrom_path)
        # Get a nonexist domain name
        vm_name = "vol_install_test"

    try:
        pvtest = utlv.PoolVolumeTest(test, params)
        pvtest.pre_pool(pool_name, pool_type, pool_target, emulated_img,
                        image_size=emulated_size, pre_disk_vol=[volume_size],
                        device_name=block_device)

        logging.debug("Current pools:\n%s",
                      libvirt_storage.StoragePool().list_pools())

        new_pool = libvirt_storage.PoolVolume(pool_name)
        if pool_type == "disk":
            volumes = new_pool.list_volumes()
            logging.debug("Current volumes:%s", volumes)
        else:
            volumes = create_volumes(test, new_pool, volume_count, volume_size)
        if application == "attach":
            vm = env.get_vm(vm_name)
            session = vm.wait_for_login()
            virsh.attach_disk(vm_name, list(volumes.values())[volume_count - 1],
                              disk_target, extra="--subdriver raw")
            vm_attach_device = "/dev/%s" % disk_target
            if session.cmd_status("which parted"):
                # No parted command, check device only
                if session.cmd_status("ls %s" % vm_attach_device):
                    test.fail("Didn't find attached device:%s"
                              % vm_attach_device)
                return
            # Test if attached disk can be used normally
            time.sleep(10)  # Need seconds for the new disk to be recognized
            utlv.mk_part(vm_attach_device, session=session)
            session.cmd("mkfs.ext4 %s1" % vm_attach_device)
            session.cmd("mount %s1 /mnt" % vm_attach_device)
            session.cmd("echo %s > /mnt/test" % test_message)
            output = session.cmd_output("cat /mnt/test").strip()
            if output != test_message:
                test.fail("%s cannot be used normally!"
                          % vm_attach_device)
            session.cmd("umount /mnt")
        elif application == "install":
            # Get a nonexist domain name anyway
            while virsh.domain_exists(vm_name):
                vm_name += "_test"
            # Prepare installation parameters
            params["main_vm"] = vm_name
            vm = env.create_vm("libvirt", None, vm_name, params,
                               test.bindir)
            env.register_vm(vm_name, vm)
            params["image_name"] = list(volumes.values())[volume_count - 1]
            params["image_format"] = "raw"
            params['force_create_image'] = "yes"
            params['remove_image'] = "yes"
            params['shutdown_cleanly'] = "yes"
            params['shutdown_cleanly_timeout'] = 120
            params['guest_port_unattended_install'] = 12323
            params['inactivity_watcher'] = "error"
            params['inactivity_treshold'] = 1800
            params['image_verify_bootable'] = "no"
            params['unattended_delivery_method'] = "cdrom"
            params['drive_index_unattended'] = 1
            params['drive_index_cd1'] = 2
            params['boot_once'] = "d"
            params['medium'] = "cdrom"
            params['wait_no_ack'] = "yes"
            params['image_raw_device'] = "yes"
            params['backup_image_before_testing'] = "no"
            params['kernel_params'] = ("ks=cdrom nicdelay=60 "
                                       "console=ttyS0,115200 console=tty0")
            params['cdroms'] = "unattended cd1"
            params['redirs'] += " unattended_install"
            selinux_mode = None
            try:
                selinux_mode = utils_selinux.get_status()
                utils_selinux.set_status("permissive")
                try:
                    unattended_install.run(test, params, env)
                except process.CmdError as detail:
                    test.fail("Guest install failed:%s" % detail)
            finally:
                if selinux_mode is not None:
                    utils_selinux.set_status(selinux_mode)
                env.unregister_vm(vm_name)
    finally:
        try:
            if application == "install":
                if virsh.domain_exists(vm_name):
                    virsh.remove_domain(vm_name)
            elif application == "attach":
                virsh.detach_disk(vm_name, disk_target)
                utils_misc.wait_for(
                   lambda: not utlv.device_exists(vm, disk_target), 10)
        finally:
            pvtest.cleanup_pool(pool_name, pool_type,
                                pool_target, emulated_img,
                                device_name=block_device)
