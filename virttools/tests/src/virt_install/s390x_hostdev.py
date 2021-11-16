import os
import logging
from avocado.core import data_dir
from virttest.utils_zcrypt import CryptoDeviceInfoBuilder, \
    APMaskHelper, load_vfio_ap, unload_vfio_ap
from provider.vfio import ccw
from uuid import uuid4
from virttest import storage

def run(test, env, params):
    disk = storage.get_image_filename_filesytem(env, data_dir.get_data_dir()))
    
    pass

def __fake_run(test, env, params):
    """
    Import machine with supported --hostdevs on s390x.
    Start the machine and confirm the passthrough.
    """

    ccw.assure_preconditions()
    schid, chpids = ccw.get_device_info()
    uuid = str(uuid4())
    ccw.set_override(schid)
    ccw.start_device(uuid, schid)


    load_vfio_ap()
    info = CryptoDeviceInfoBuilder.get()

    devices = [info.domains[0]]
    mask_helper = APMaskHelper.from_infos(devices)
    matrix_dev = MatrixDevice.from_infos(devices)

    result = vires.nodedev_list(cap="mdev", debug=True)
    logging.debug(result, str(result))
    
    """
    vm.start()

    if not ccw.device_is_listed(session, chpids):
        test.fail("CCW device not listed")
    """
    try:
        pass
    finally:
        if matrix_dev:
            matrix_dev.unassign_all()
        if mask_helper:
            mask_helper.unassign_all()
        unload_vfio_ap()
        if uuid:
            ccw.stop_device(uuid)
        if schid:
            ccw.unset_override(schid)
