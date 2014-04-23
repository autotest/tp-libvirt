import logging
from autotest.client.shared import error
from virttest import libvirt_storage
from virttest import virsh
from virttest.utils_test import libvirt as utlv
from virttest.tests import unattended_install


def create_volumes(new_pool, volume_count, volume_size):
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
            raise error.TestFail("Create volume %s failed." % vol_name)
        volumes = new_pool.list_volumes()
        logging.debug("Current volumes:%s", volumes)
        if vol_name in volumes.keys():
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
    emulated_img = params.get("emulated_img", "emulated_img")
    volume_count = int(params.get("volume_count", 1))
    volume_size = params.get("volume_size", "4G")
    emulated_size = "%sG" % (volume_count * int(volume_size[:-1]) + 1)
    application =  params.get("application", "install")
    disk_target = params.get("disk_target", "vdb")
    test_message = params.get("test_message", "")
    vm_name = params.get("main_vm", "virt-tests-vm1")
    if application == "install":
        vm_name = params.get("vm_name", "vm1")

    try:
        pvtest = utlv.PoolVolumeTest(test, params)
        pvtest.pre_pool(pool_name, pool_type, pool_target, emulated_img,
                        emulated_size)

        logging.debug("Current pools:\n%s",
                      libvirt_storage.StoragePool().list_pools())

        new_pool = libvirt_storage.PoolVolume(pool_name)
        volumes = create_volumes(new_pool, volume_count, volume_size)
        if application == "attach":
            vm = env.get_vm(vm_name)
            session = vm.wait_for_login()
            # The attach-disk action based on running guest,
            # so no need to recover the guest, it will be
            # recovered automatically after shutdown/reboot
            virsh.attach_disk(vm_name, volumes.values()[volume_count-1],
                              disk_target)
            vm_attach_device = "/dev/%s" % disk_target
            # Test if attached disk can be used normally
            utlv.mk_part(vm_attach_device, session=session)
            session.cmd("mkfs.ext4 %s1" % vm_attach_device)
            session.cmd("mount %s1 /mnt" % vm_attach_device)
            session.cmd("echo %s > /mnt/test" % test_message)
            output = session.cmd_output("cat /mnt/test").strip()
            if output != test_message:
                raise error.TestFail("%s cannot be used normally!"
                                     % vm_attach_device)
        elif application == "install":
            # Get a nonexist domain name
            while virsh.domain_exists(vm_name):
                vm_name += "_test"
            params["image_name"] = volumes.values()[volume_count-1]
            try:
                unattended_install.run(test, params, env)
            except error.CmdError, detail:
                raise error.TestFail("Guest install failed:%s" % detail)
    finally:
        try:
            if application == "install":
                if virsh.domain_exists(vm_name):
                    virsh.remove_domain(vm_name)
        finally:
            pvtest.cleanup_pool(pool_name, pool_type,
                                pool_target, emulated_img)
