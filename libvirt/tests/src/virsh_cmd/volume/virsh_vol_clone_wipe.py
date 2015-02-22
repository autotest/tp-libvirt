import os
import random
import logging
from autotest.client import utils
from autotest.client.shared import error
from virttest.utils_test import libvirt
from virttest import libvirt_storage
from virttest import virsh
from virttest import utils_misc
from provider import libvirt_version


def run(test, params, env):
    """
    This test cover two volume commands: vol-clone and vol-wipe.

    1. Create a given type pool.
    2. Create a given format volume in the pool.
    3. Clone the new create volume.
    4. Wipe the new clone volume.
    5. Delete the volume and pool.
    """

    pool_name = params.get("pool_name")
    pool_type = params.get("pool_type")
    pool_target = params.get("pool_target")
    if not os.path.dirname(pool_target):
        pool_target = os.path.join(test.tmpdir, pool_target)
    emulated_image = params.get("emulated_image")
    emulated_image_size = params.get("emulated_image_size")
    vol_name = params.get("vol_name")
    new_vol_name = params.get("new_vol_name")
    vol_capability = params.get("vol_capability")
    vol_format = params.get("vol_format")
    clone_option = params.get("clone_option", "")
    wipe_algorithms = params.get("wipe_algorithms")

    if virsh.has_command_help_match("vol-wipe", "--prealloc-metadata") is None:
        if "prealloc-metadata" in clone_option:
            raise error.TestNAError("Option --prealloc-metadata "
                                    "is not supported.")

    # Using algorithms other than zero need scrub installed.
    try:
        utils_misc.find_command('scrub')
    except ValueError:
        logging.warning("Can't locate scrub binary, only 'zero' algorithm "
                        "is used.")
        valid_algorithms = ["zero"]
    else:
        valid_algorithms = ["zero", "nnsa", "dod", "bsi", "gutmann",
                            "schneier", "pfitzner7", "pfitzner33", "random"]

    # Choose an algorithms randomly
    if wipe_algorithms:
        alg = random.choice(wipe_algorithms.split())
    else:
        alg = random.choice(valid_algorithms)

    clone_status_error = "yes" == params.get("clone_status_error", "no")
    wipe_status_error = "yes" == params.get("wipe_status_error", "no")

    # libvirt acl polkit related params
    uri = params.get("virsh_uri")
    unpri_user = params.get('unprivileged_user')
    if unpri_user:
        if unpri_user.count('EXAMPLE'):
            unpri_user = 'testacl'

    if not libvirt_version.version_compare(1, 1, 1):
        if params.get('setup_libvirt_polkit') == 'yes':
            raise error.TestNAError("API acl test not supported in current"
                                    " libvirt version.")

    del_pool = True
    libv_pvt = libvirt.PoolVolumeTest(test, params)
    try:
        libv_pool = libvirt_storage.StoragePool()
        while libv_pool.pool_exists(pool_name):
            logging.debug("Use exist pool '%s'", pool_name)
            del_pool = False
        else:
            # Create a new pool
            disk_vol = []
            if pool_type == 'disk':
                disk_vol.append(params.get("pre_vol", '10M'))
            libv_pvt.pre_pool(pool_name=pool_name,
                              pool_type=pool_type,
                              pool_target=pool_target,
                              emulated_image=emulated_image,
                              image_size=emulated_image_size,
                              pre_disk_vol=disk_vol)
        libv_vol = libvirt_storage.PoolVolume(pool_name)
        if libv_vol.volume_exists(vol_name):
            logging.debug("Use exist volume '%s'", vol_name)
        elif vol_format in ['raw', 'qcow2', 'qed', 'vmdk']:
            # Create a new volume
            libv_pvt.pre_vol(vol_name=vol_name,
                             vol_format=vol_format,
                             capacity=vol_capability,
                             allocation=None,
                             pool_name=pool_name)
        elif vol_format == 'partition':
            vol_name = libv_vol.list_volumes().keys()[0]
            logging.debug("Partition %s in disk pool is volume" % vol_name)
        elif vol_format == 'sparse':
            # Create a sparse file in pool
            sparse_file = pool_target + '/' + vol_name
            cmd = "dd if=/dev/zero of=" + sparse_file
            cmd += " bs=1 count=0 seek=" + vol_capability
            utils.run(cmd)
        else:
            raise error.TestError("Unknown volume format %s" % vol_format)
        # Refresh the pool
        virsh.pool_refresh(pool_name)
        vol_info = libv_vol.volume_info(vol_name)
        for key in vol_info:
            logging.debug("Original volume info: %s = %s", key, vol_info[key])

        # Metadata preallocation is not support for block volume
        if vol_info["Type"] == "block" and clone_option.count("prealloc-metadata"):
            clone_status_error = True

        # Clone volume
        clone_result = virsh.vol_clone(vol_name, new_vol_name, pool_name,
                                       clone_option, debug=True)
        if not clone_status_error:
            if clone_result.exit_status != 0:
                raise error.TestFail("Clone volume fail:\n%s" %
                                     clone_result.stdout.strip())
            else:
                vol_info = libv_vol.volume_info(new_vol_name)
                for key in vol_info:
                    logging.debug("Cloned volume info: %s = %s", key,
                                  vol_info[key])
                logging.debug("Clone volume successfully.")
                # Wipe the new clone volume
                if alg:
                    logging.debug("Wiping volume by '%s' algorithm", alg)
                wipe_result = virsh.vol_wipe(new_vol_name, pool_name, alg,
                                             unprivileged_user=unpri_user,
                                             uri=uri, debug=True)
                if not wipe_status_error:
                    if wipe_result.exit_status != 0:
                        raise error.TestFail("Wipe volume fail:\n%s" %
                                             clone_result.stdout.strip())
                    else:
                        virsh_vol_info = libv_vol.volume_info(new_vol_name)
                        for key in virsh_vol_info:
                            logging.debug("Wiped volume info(virsh): %s = %s",
                                          key, virsh_vol_info[key])
                        vol_path = virsh.vol_path(new_vol_name,
                                                  pool_name).stdout.strip()
                        qemu_vol_info = utils_misc.get_image_info(vol_path)
                        for key in qemu_vol_info:
                            logging.debug("Wiped volume info(qemu): %s = %s",
                                          key, qemu_vol_info[key])
                            if qemu_vol_info['format'] != 'raw':
                                raise error.TestFail("Expect wiped volume "
                                                     "format is raw")
                elif wipe_status_error and wipe_result.exit_status == 0:
                    raise error.TestFail("Expect wipe volume fail, but run"
                                         " successfully.")
        elif clone_status_error and clone_result.exit_status == 0:
            raise error.TestFail("Expect clone volume fail, but run"
                                 " successfully.")
    finally:
        # Clean up
        try:
            if del_pool:
                libv_pvt.cleanup_pool(pool_name, pool_type, pool_target,
                                      emulated_image)
            else:
                # Only delete the volumes
                libv_vol = libvirt_storage.PoolVolume(pool_name)
                for vol in [vol_name, new_vol_name]:
                    libv_vol.delete_volume(vol)
        except error.TestFail, detail:
            logging.error(str(detail))
