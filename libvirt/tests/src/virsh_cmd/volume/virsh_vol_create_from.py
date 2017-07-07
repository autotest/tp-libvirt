import logging
import os

from autotest.client.shared import error

from virttest import virsh
from virttest import libvirt_storage
from virttest.utils_test import libvirt as utlv

from provider import libvirt_version


def run(test, params, env):
    """
    Test virsh vol-create-from command to cover the following matrix:

    pool = [source, destination]
    pool_type = [dir, disk, fs, logical, netfs, iscsi, scsi]
    volume_format = [raw, qcow2, qed]

    Note, both 'iscsi' and 'scsi' type pools don't support create volume by
    virsh, so which can't be destination pools. And for disk pool, it can't
    create volume with specified format.
    """

    src_pool_type = params.get("src_pool_type")
    src_pool_target = params.get("src_pool_target")
    src_emulated_image = params.get("src_emulated_image")
    src_vol_format = params.get("src_vol_format")
    dest_pool_type = params.get("dest_pool_type")
    dest_pool_target = params.get("dest_pool_target")
    dest_emulated_image = params.get("dest_emulated_image")
    dest_vol_format = params.get("dest_vol_format")
    prealloc_option = params.get("prealloc_option")
    status_error = params.get("status_error", "no")

    if not libvirt_version.version_compare(1, 0, 0):
        if "--prealloc-metadata" in prealloc_option:
            raise error.TestNAError("metadata preallocation not supported in"
                                    " current libvirt version.")

    vol_file = ""
    try:
        # Create the src/dest pool
        src_pool_name = "virt-%s-pool" % src_pool_type
        dest_pool_name = "virt-%s-pool" % dest_pool_type

        pvt = utlv.PoolVolumeTest(test, params)
        pvt.pre_pool(src_pool_name, src_pool_type, src_pool_target,
                     src_emulated_image, image_size="40M",
                     pre_disk_vol=["1M"])

        if src_pool_type != dest_pool_type:
            pvt.pre_pool(dest_pool_name, dest_pool_type, dest_pool_target,
                         dest_emulated_image, image_size="100M",
                         pre_disk_vol=["1M"])

        # Print current pools for debugging
        logging.debug("Current pools:%s",
                      libvirt_storage.StoragePool().list_pools())

        # Create the src vol
        # 8M is the minimal size for logical volume(PPC)
        # 4M is the minimal size for logical volume(x86)
        vol_size = params.get("image_volume_size", "16777216")
        if src_pool_type in ["dir", "logical", "netfs", "fs"]:
            src_vol_name = "src_vol"
            pvt.pre_vol(vol_name=src_vol_name, vol_format=src_vol_format,
                        capacity=vol_size, allocation=None,
                        pool_name=src_pool_name)
        else:
            src_vol_name = utlv.get_vol_list(src_pool_name).keys()[0]
        # Prepare vol xml file
        dest_vol_name = "dest_vol"
        # According to BZ#1138523, we need inpect the right name
        # (disk partition) for new volume
        if dest_pool_type == "disk":
            dest_vol_name = utlv.new_disk_vol_name(dest_pool_name)
            if dest_vol_name is None:
                raise error.TestError("Fail to generate volume name")
        if dest_pool_type == "disk":
            dest_vol_format = ""
            prealloc_option = ""
        vol_xml = """
<volume>
  <name>%s</name>
  <capacity unit='bytes'>%s</capacity>
  <target>
    <format type='%s'/>
  </target>
</volume>
""" % (dest_vol_name, vol_size, dest_vol_format)
        logging.debug("Prepare the volume xml: %s", vol_xml)
        vol_file = os.path.join(test.tmpdir, "dest_vol.xml")
        xml_object = open(vol_file, 'w')
        xml_object.write(vol_xml)
        xml_object.close()

        # iSCSI and SCSI type pool can't create vols via virsh
        if dest_pool_type in ["iscsi", "scsi"]:
            raise error.TestFail("Unsupport create vol for %s type pool"
                                 % dest_pool_type)
        # Metadata preallocation is not supported for block volumes
        if dest_pool_type in ["disk", "logical"]:
            prealloc_option = ""
        # Run run_virsh_vol_create_from to create dest vol
        cmd_result = virsh.vol_create_from(dest_pool_name, vol_file,
                                           src_vol_name, src_pool_name,
                                           prealloc_option, ignore_status=True,
                                           debug=True)
        status = cmd_result.exit_status

        # Check result
        if status_error == "no":
            if status == 0:
                dest_pv = libvirt_storage.PoolVolume(dest_pool_name)
                dest_volumes = dest_pv.list_volumes().keys()
                logging.debug("Current volumes in %s: %s",
                              dest_pool_name, dest_volumes)
                if dest_vol_name not in dest_volumes:
                    raise error.TestFail("Can't find volume: % from pool: %s"
                                         % (dest_vol_name, dest_pool_name))
            else:
                raise error.TestFail(cmd_result.stderr)
        else:
            if status:
                logging.debug("Expect error: %s", cmd_result.stderr)
            else:
                raise error.TestFail("Expect fail, but run successfully!")
    finally:
        # Cleanup: both src and dest should be removed
        try:
            pvt.cleanup_pool(src_pool_name, src_pool_type, src_pool_target,
                             src_emulated_image)
        except error.TestFail, detail:
            logging.error(str(detail))
        if src_pool_type != dest_pool_type:
            pvt.cleanup_pool(dest_pool_name, dest_pool_type, dest_pool_target,
                             dest_emulated_image)
        if os.path.isfile(vol_file):
            os.remove(vol_file)
