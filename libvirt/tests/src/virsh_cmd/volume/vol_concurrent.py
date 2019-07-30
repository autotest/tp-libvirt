import logging
import os
import threading
import re
try:
    import queue as Queue
except ImportError:
    import Queue

from avocado.core import exceptions

from virttest import virsh
from virttest import data_dir
from virttest import libvirt_storage
from virttest.utils_test import libvirt as utlv


q = Queue.Queue()


def prepare_vol_xml(vol_name, vol_size, vol_format):
    """
    Prepare vol xml for vol_create_from as input

    :param vol_name: The name of new volume
    :param vol_size: The volume size
    :param vol_format: The volume format
    :return: The vol file path
    """
    vol_xml = """
<volume>
  <name>%s</name>
  <capacity unit='bytes'>%s</capacity>
  <target>
    <format type='%s'/>
  </target>
</volume>
""" % (vol_name, vol_size, vol_format)
    vol_file = os.path.join(data_dir.get_tmp_dir(), "%s.xml" % vol_name)
    with open(vol_file, 'w') as xml_object:
        xml_object.write(vol_xml)
    return vol_file


def worker(func_name, is_save, *args):
    """
    The general func to perform different volume operation

    :param func_name: The name of volume func
    :param is_save: True or False. Whether to save the running result
    :param args: The func argument
    """
    logging.debug("%s run..." % func_name)
    result = func_name(*args)
    if is_save:
        q.put((result, func_name))


def check_cmd_output(cmd_result, expect_error, test):
    """
    Check vol command output expected or not

    :param cmd_result: The cmdResult object from virsh.vol_xxx
    :param expect_error: True or False. Expect error or not
    :return: If output correct, return True. Otherwise False
    """
    if not expect_error:
        cmd_output = cmd_result.stdout
    else:
        cmd_output = cmd_result.stderr
        if not re.search("volume.*(in use|being allocated)", cmd_output):
            test.fail("Not expect output:\n%s" % cmd_output)
    logging.debug("Cmd output as expected:\n%s" % cmd_output)


def run(test, params, env):
    """
    Test simultaneous volume operations
    1. Create a pool and a volume
    2. Create n number of volumes simultaneously whose source volume is
       the same
    3. Run virsh.vol_xxx before step2 volume is creating
       vol-create
       vol-clone
       vol-download
       vol-upload
       vol-delete
       vol-wipe
       vol-resize
    4. Check if success or error throw as expected
    """

    src_pool_name = params.get("pool_name")
    pool_type = params.get("pool_type", "dir")
    pool_target = params.get("pool_target")
    pool_target = os.path.join(data_dir.get_tmp_dir(), pool_target)
    vol_format = params.get("volume_format")
    vol_size = params.get("volume_size", "262144")
    volume_count = int(params.get("volume_number"))
    new_capacity = params.get("new_capacity")
    emulated_image = params.get("emulated_image", "emulated-image")
    emulated_image_size = params.get("emulated_image_size")
    status_error = ("yes" == params.get("status_error", "no"))
    file_to_clean = []

    updown_load_f = params.get("to_file")
    if updown_load_f:
        updown_load_f = os.path.join(data_dir.get_tmp_dir(), updown_load_f)
        file_to_clean.append(updown_load_f)
        if os.path.basename(updown_load_f) == "upload_file":
            open(updown_load_f, "w").close()

    pvt = utlv.PoolVolumeTest(test, params)
    try:
        # Create the src pool
        pvt.pre_pool(src_pool_name, pool_type, pool_target,
                     emulated_image, image_size=emulated_image_size)

        # Print current pools for debugging
        logging.debug("Current pools:%s",
                      libvirt_storage.StoragePool().list_pools())

        # Create the src vol
        src_vol_name = params.get("volume_name")
        pv = libvirt_storage.PoolVolume(src_pool_name)
        pv.create_volume(src_vol_name, vol_size, None, vol_format)

        # Create n number of volumes simultaneously
        threads = []
        new_vol_list = []
        for count in range(1, volume_count+1):
            new_vol_name = "new_vol%s" % count
            vol_file = prepare_vol_xml(new_vol_name, vol_size, vol_format)
            file_to_clean.append(vol_file)
            new_vol_list.append(new_vol_name)

            # Run virsh_vol_create_from in parallel
            create_t = threading.Thread(target=worker,
                                        args=(virsh.vol_create_from, False,
                                              src_pool_name, vol_file,
                                              src_vol_name, src_pool_name))
            threads.append(create_t)

        # Run virsh.vol_xxx when volume is creating
        vol_status = params.get("vol_status")
        operate_vol = ""
        if vol_status == "reading":
            operate_vol = src_vol_name

        elif vol_status == "writing":
            operate_vol = "new_vol%s" % volume_count

        vol_operation = params.get("vol_operation")
        operation_t = ""
        if vol_operation == "create":
            vol_file = prepare_vol_xml("vol-create", vol_size, vol_format)
            file_to_clean.append(vol_file)
            operation_t = threading.Thread(target=worker,
                                           args=(virsh.vol_create_from, True,
                                                 src_pool_name, vol_file,
                                                 operate_vol, src_pool_name))
        elif vol_operation == "delete":
            operation_t = threading.Thread(target=worker,
                                           args=(virsh.vol_delete, True,
                                                 operate_vol, src_pool_name))
        elif vol_operation == "wipe":
            operation_t = threading.Thread(target=worker,
                                           args=(virsh.vol_wipe, True,
                                                 operate_vol, src_pool_name))
        elif vol_operation == "resize":
            operation_t = threading.Thread(target=worker,
                                           args=(virsh.vol_resize, True,
                                                 operate_vol, new_capacity, src_pool_name))
        elif vol_operation == "clone":
            operation_t = threading.Thread(target=worker,
                                           args=(virsh.vol_clone, True,
                                                 operate_vol, "vol-clone", src_pool_name))
        elif vol_operation == "upload":
            operation_t = threading.Thread(target=worker,
                                           args=(virsh.vol_upload, True,
                                                 operate_vol, updown_load_f, src_pool_name))
        elif vol_operation == "download":
            operation_t = threading.Thread(target=worker,
                                           args=(virsh.vol_download, True,
                                                 operate_vol, updown_load_f, src_pool_name))

        is_started = False
        for t in threads:
            t.start()
        while threading.active_count() > 1:
            if pv.volume_exists(operate_vol):
                operation_t.start()
                is_started = True
                break
        if not is_started:
            test.cancel("The creation of volume completed already")
        for t in threads:
            t.join()
        operation_t.join()

        # Print current volumes for debugging
        logging.debug("Current volumes:%s", pv.list_volumes())

        # Check volume creation
        for vol_name in new_vol_list:
            if vol_name not in pv.list_volumes():
                test.fail("Failed to create volume %s" % vol_name)

        # Check each thread running result
        result_list = []
        while not q.empty():
            result_list.append(q.get())
        for item in result_list:
            if vol_operation in item[1].__name__:
                if not status_error and item[0].exit_status:
                    test.fail("%s failed:\n%s" % (item[1], item[0].stderr))
                elif status_error and not item[0].exit_status:
                    test.fail("%s expect to fail but succeed:\n%s" % (item[1], item[0].stdout))
                check_cmd_output(item[0], status_error, test)
    finally:
        # Cleanup: both src and dest should be removed
        try:
            pvt.cleanup_pool(src_pool_name, pool_type, pool_target,
                             emulated_image)
        except exceptions.TestFail as detail:
            logging.error(str(detail))
        logging.debug("clean file: %s", str(file_to_clean))
        for file in file_to_clean:
            if os.path.exists(file):
                os.remove(file)
