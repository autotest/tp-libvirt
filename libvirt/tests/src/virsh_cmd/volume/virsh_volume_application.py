import logging
from autotest.client.shared import error
from virttest import libvirt_storage
from virttest.utils_test import libvirt as utlv


def create_volumes(pv, volume_count, volume_size):
    count = 1
    created_volumes = {}
    while count <= volume_count:
        vol_name = "volume%s" % count
        count += 1
        # TODO: Check whether there is sufficient space.
        if not pv.create_volume(vol_name, volume_size):
            raise error.TestFail("Create volume %s failed." % vol_name)
        volumes = pv.list_volumes()
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

    try:
        pvtest = utlv.PoolVolumeTest(test, params)
        pvtest.pre_pool(pool_name, pool_type, pool_target, emulated_img,
                        emulated_size)

        logging.debug("Current pools:\n%s",
                      libvirt_storage.StoragePool().list_pools())

        pv = libvirt_storage.PoolVolume(pool_name)
        volumes = create_volumes(pv, volume_count, volume_size)
    finally:
        pvtest.cleanup_pool(pool_name, pool_type, pool_target, emulated_img)
