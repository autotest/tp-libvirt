import os
import time

from virttest import data_dir
from virttest import utils_misc
from avocado.utils import process


def run(test, params, env):
    """
    Use qemu-nbd to read and modify a qcow2 file

    1) qemu-img create test.qcow2 -f qcow2 1G
    2) nbdfuse mountpoint/nbdtest/ --socket-activation qemu-nbd -f qcow2 test.qcow2
    3) check image format is 'raw' in mountpoint/nbdtest/
    4) fusermount -u mountpoint/nbdtest/
    """
    image_qcow2_size = params.get('image_qcow2_size', '512M')
    image_qcow2_path = params.get('image_qcow2_path')

    nbdfuse_mp_filename = params.get('nbdfuse_mp_filename', '')

    try:
        temp_dir = data_dir.get_tmp_dir()
        # mountpoint of nbdfuse
        nbdfuse_mp = os.path.join(temp_dir, "nbdfuse_mp")
        if not os.path.exists(nbdfuse_mp):
            os.makedirs(nbdfuse_mp)

        if not image_qcow2_path:
            image_qcow2_path = os.path.join(temp_dir, "nbdfuse_test.qcow2")
        nbdfuse_mp_filename = os.path.join(nbdfuse_mp, nbdfuse_mp_filename)

        # Prepare and qcow2 image
        cmd = "qemu-img create %s -f qcow2 %s" % (
            image_qcow2_path, image_qcow2_size)
        process.run(cmd, verbose=True, ignore_status=False, shell=True)
        image_info_qcow2 = utils_misc.get_image_info(image_qcow2_path)

        # Must have the '&' at the end
        nbdfuse_cmd = "nbdfuse %s --socket-activation qemu-nbd -f qcow2 %s &" % (
            nbdfuse_mp_filename, image_qcow2_path)
        # Must set ignore_bg_processes=True because nbdfuse is serving like
        # a deamon at background
        # Must set shell=True
        process.run(
            nbdfuse_cmd,
            verbose=True,
            ignore_status=True,
            shell=True,
            ignore_bg_processes=True)

        # A protective sleep because above command was ran in background
        time.sleep(3)
        # If nbdfuse_mp_filename is '', change it to nbdfuse's default name
        # 'nbd'
        if nbdfuse_mp_filename.rstrip(os.sep) == nbdfuse_mp.rstrip(os.sep):
            nbdfuse_mp_filename = os.path.join(nbdfuse_mp_filename, 'nbd')

        image_info_raw = utils_misc.wait_for(
            lambda: utils_misc.get_image_info(nbdfuse_mp_filename), timeout=60)

        if not image_info_raw or image_info_raw['format'] != 'raw' or image_info_raw[
                'vsize'] != image_info_qcow2['vsize']:
            test.fail("nbdfuse test failed: %s" % image_info_raw)
    finally:
        nbdfuse_umount_cmd = "fusermount -u %s" % nbdfuse_mp
        process.run(
            nbdfuse_umount_cmd,
            verbose=True,
            ignore_status=True,
            shell=True)

        if os.path.exists(image_qcow2_path):
            os.unlink(image_qcow2_path)
