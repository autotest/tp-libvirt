import os
import re
import logging

from avocado.utils import process
from avocado.core import exceptions

from virttest import utils_misc
from virttest import data_dir
from virttest import virsh
from virttest import libvirt_storage
from virttest import utils_net

from virttest.libvirt_xml import vol_xml
from virttest.utils_test import libvirt as utlv
from virttest.staging import service

from virttest import libvirt_version


def run(test, params, env):
    """
    1. Create a pool
    2. Create n number of volumes(vol-create-as)
    3. Check the volume details from the following commands
       vol-info
       vol-key
       vol-list
       vol-name
       vol-path
       vol-pool
       qemu-img info
    4. Delete the volume and check in vol-list
    5. Repeat the steps for number of volumes given
    6. Delete the pool and target
    TODO: Handle negative testcases
    """

    def delete_volume(expected_vol):
        """
        Deletes Volume
        """
        pool_name = expected_vol['pool_name']
        vol_name = expected_vol['name']
        pv = libvirt_storage.PoolVolume(pool_name)
        if not pv.delete_volume(vol_name):
            test.fail("Delete volume failed." % vol_name)
        else:
            logging.debug("Volume: %s successfully deleted on pool: %s",
                          vol_name, pool_name)

    def get_vol_list(pool_name, vol_name):
        """
        Parse the volume list
        """
        output = virsh.vol_list(pool_name, "--details")
        rg = re.compile(
            r'^(\S+)\s+(\S+)\s+(\S+)\s+(\d+.\d+\s\S+)\s+(\d+.\d+.*)')
        vol = {}
        vols = []
        volume_detail = None
        for line in output.stdout.splitlines():
            match = re.search(rg, line.lstrip())
            if match is not None:
                vol['name'] = match.group(1)
                vol['path'] = match.group(2)
                vol['type'] = match.group(3)
                vol['capacity'] = match.group(4)
                vol['allocation'] = match.group(5)
                vols.append(vol)
                vol = {}
        for volume in vols:
            if volume['name'] == vol_name:
                volume_detail = volume
        return volume_detail

    def norm_capacity(capacity):
        """
        Normalize the capacity values to bytes
        """
        # Normaize all values to bytes
        norm_capacity = {}
        des = {'B': 'B', 'bytes': 'B', 'b': 'B', 'kib': 'K',
               'KiB': 'K', 'K': 'K', 'k': 'K', 'KB': 'K',
               'mib': 'M', 'MiB': 'M', 'M': 'M', 'm': 'M',
               'MB': 'M', 'gib': 'G', 'GiB': 'G', 'G': 'G',
               'g': 'G', 'GB': 'G', 'Gb': 'G', 'tib': 'T',
               'TiB': 'T', 'TB': 'T', 'T': 'T', 't': 'T'
               }
        val = {'B': 1,
               'K': 1024,
               'M': 1048576,
               'G': 1073741824,
               'T': 1099511627776
               }

        reg_list = re.compile(r'(\S+)\s(\S+)')
        match_list = re.search(reg_list, capacity['list'])
        if match_list is not None:
            mem_value = float(match_list.group(1))
            norm = val[des[match_list.group(2)]]
            norm_capacity['list'] = int(mem_value * norm)
        else:
            test.fail("Error in parsing capacity value in"
                      " virsh vol-list")

        match_info = re.search(reg_list, capacity['info'])
        if match_info is not None:
            mem_value = float(match_info.group(1))
            norm = val[des[match_list.group(2)]]
            norm_capacity['info'] = int(mem_value * norm)
        else:
            test.fail("Error in parsing capacity value "
                      "in virsh vol-info")

        norm_capacity['qemu_img'] = capacity['qemu_img']
        norm_capacity['xml'] = int(capacity['xml'])

        return norm_capacity

    def check_vol(expected, avail=True):
        """
        Checks the expected volume details with actual volume details from
        vol-dumpxml
        vol-list
        vol-info
        vol-key
        vol-path
        qemu-img info
        """
        error_count = 0

        pv = libvirt_storage.PoolVolume(expected['pool_name'])
        vol_exists = pv.volume_exists(expected['name'])
        if vol_exists:
            if not avail:
                error_count += 1
                logging.error("Expect volume %s not exists but find it",
                              expected['name'])
                return error_count
        else:
            if avail:
                error_count += 1
                logging.error("Expect volume %s exists but not find it",
                              expected['name'])
                return error_count
            else:
                logging.info("Volume %s checked successfully for deletion",
                             expected['name'])
                return error_count

        actual_list = get_vol_list(expected['pool_name'], expected['name'])
        actual_info = pv.volume_info(expected['name'])
        # Get values from vol-dumpxml
        volume_xml = vol_xml.VolXML.new_from_vol_dumpxml(expected['name'],
                                                         expected['pool_name'])

        # Check against virsh vol-key
        vol_key = virsh.vol_key(expected['name'], expected['pool_name'])
        if vol_key.stdout.strip() != volume_xml.key:
            logging.error("Volume key is mismatch \n%s"
                          "Key from xml: %s\nKey from command: %s",
                          expected['name'], volume_xml.key, vol_key)
            error_count += 1
        else:
            logging.debug("virsh vol-key for volume: %s successfully"
                          " checked against vol-dumpxml", expected['name'])

        # Check against virsh vol-name
        get_vol_name = virsh.vol_name(expected['path'])
        if get_vol_name.stdout.strip() != expected['name']:
            logging.error("Volume name mismatch\n"
                          "Expected name: %s\nOutput of vol-name: %s",
                          expected['name'], get_vol_name)

        # Check against virsh vol-path
        vol_path = virsh.vol_path(expected['name'], expected['pool_name'])
        if expected['path'] != vol_path.stdout.strip():
            logging.error("Volume path mismatch for volume: %s\n"
                          "Expected path: %s\nOutput of vol-path: %s\n",
                          expected['name'],
                          expected['path'], vol_path)
            error_count += 1
        else:
            logging.debug("virsh vol-path for volume: %s successfully checked"
                          " against created volume path", expected['name'])

        # Check path against virsh vol-list
        if expected['path'] != actual_list['path']:
            logging.error("Volume path mismatch for volume:%s\n"
                          "Expected Path: %s\nPath from virsh vol-list: %s",
                          expected['name'], expected['path'],
                          actual_list['path'])
            error_count += 1
        else:
            logging.debug("Path of volume: %s from virsh vol-list "
                          "successfully checked against created "
                          "volume path", expected['name'])

        # Check path against virsh vol-dumpxml
        if expected['path'] != volume_xml.path:
            logging.error("Volume path mismatch for volume: %s\n"
                          "Expected Path: %s\nPath from virsh vol-dumpxml: %s",
                          expected['name'], expected['path'], volume_xml.path)
            error_count += 1

        else:
            logging.debug("Path of volume: %s from virsh vol-dumpxml "
                          "successfully checked against created volume path",
                          expected['name'])

        # Check type against virsh vol-list
        if expected['type'] != actual_list['type']:
            logging.error("Volume type mismatch for volume: %s\n"
                          "Expected Type: %s\n Type from vol-list: %s",
                          expected['name'], expected['type'],
                          actual_list['type'])
            error_count += 1
        else:
            logging.debug("Type of volume: %s from virsh vol-list "
                          "successfully checked against the created "
                          "volume type", expected['name'])

        # Check type against virsh vol-info
        if expected['type'] != actual_info['Type']:
            logging.error("Volume type mismatch for volume: %s\n"
                          "Expected Type: %s\n Type from vol-info: %s",
                          expected['name'], expected['type'],
                          actual_info['Type'])
            error_count += 1
        else:
            logging.debug("Type of volume: %s from virsh vol-info successfully"
                          " checked against the created volume type",
                          expected['name'])

        # Check name against virsh vol-info
        if expected['name'] != actual_info['Name']:
            logging.error("Volume name mismatch for volume: %s\n"
                          "Expected name: %s\n Name from vol-info: %s",
                          expected['name'],
                          expected['name'], actual_info['Name'])
            error_count += 1
        else:
            logging.debug("Name of volume: %s from virsh vol-info successfully"
                          " checked against the created volume name",
                          expected['name'])

        # Check format from against qemu-img info
        img_info = utils_misc.get_image_info(expected['path'])
        if expected['format']:
            if expected['format'] != img_info['format']:
                logging.error("Volume format mismatch for volume: %s\n"
                              "Expected format: %s\n"
                              "Format from qemu-img info: %s",
                              expected['name'], expected['format'],
                              img_info['format'])
                error_count += 1
            else:
                logging.debug("Format of volume: %s from qemu-img info "
                              "checked successfully against the created "
                              "volume format", expected['name'])

        # Check format against vol-dumpxml
        if expected['format']:
            if expected['format'] != volume_xml.format:
                logging.error("Volume format mismatch for volume: %s\n"
                              "Expected format: %s\n"
                              "Format from vol-dumpxml: %s",
                              expected['name'], expected['format'],
                              volume_xml.format)
                error_count += 1
            else:
                logging.debug("Format of volume: %s from virsh vol-dumpxml "
                              "checked successfully against the created"
                              " volume format", expected['name'])

        logging.info(expected['encrypt_format'])
        # Check encrypt against vol-dumpxml
        if expected['encrypt_format']:
            # As the 'default' format will change to specific valut(qcow), so
            # just output it here
            logging.debug("Encryption format of volume '%s' is: %s",
                          expected['name'], volume_xml.encryption.format)
            # And also output encryption secret uuid
            secret_uuid = volume_xml.encryption.secret['uuid']
            logging.debug("Encryption secret of volume '%s' is: %s",
                          expected['name'], secret_uuid)
            if expected['encrypt_secret']:
                if expected['encrypt_secret'] != secret_uuid:
                    logging.error("Encryption secret mismatch for volume: %s\n"
                                  "Expected secret uuid: %s\n"
                                  "Secret uuid from vol-dumpxml: %s",
                                  expected['name'], expected['encrypt_secret'],
                                  secret_uuid)
                    error_count += 1
                else:
                    # If no set encryption secret value, automatically
                    # generate a secret value at the time of volume creation
                    logging.debug("Volume encryption secret is %s", secret_uuid)

        # Check pool name against vol-pool
        vol_pool = virsh.vol_pool(expected['path'])
        if expected['pool_name'] != vol_pool.stdout.strip():
            logging.error("Pool name mismatch for volume: %s against"
                          "virsh vol-pool", expected['name'])
            error_count += 1
        else:
            logging.debug("Pool name of volume: %s checked successfully"
                          " against the virsh vol-pool", expected['name'])

        norm_cap = {}
        capacity = {}
        capacity['list'] = actual_list['capacity']
        capacity['info'] = actual_info['Capacity']
        capacity['xml'] = volume_xml.capacity
        capacity['qemu_img'] = img_info['vsize']
        norm_cap = norm_capacity(capacity)
        delta_size = int(params.get('delta_size', "1024"))
        if abs(expected['capacity'] - norm_cap['list']) > delta_size:
            logging.error("Capacity mismatch for volume: %s against virsh"
                          " vol-list\nExpected: %s\nActual: %s",
                          expected['name'], expected['capacity'],
                          norm_cap['list'])
            error_count += 1
        else:
            logging.debug("Capacity value checked successfully against"
                          " virsh vol-list for volume %s", expected['name'])

        if abs(expected['capacity'] - norm_cap['info']) > delta_size:
            logging.error("Capacity mismatch for volume: %s against virsh"
                          " vol-info\nExpected: %s\nActual: %s",
                          expected['name'], expected['capacity'],
                          norm_cap['info'])
            error_count += 1
        else:
            logging.debug("Capacity value checked successfully against"
                          " virsh vol-info for volume %s", expected['name'])

        if abs(expected['capacity'] - norm_cap['xml']) > delta_size:
            logging.error("Capacity mismatch for volume: %s against virsh"
                          " vol-dumpxml\nExpected: %s\nActual: %s",
                          expected['name'], expected['capacity'],
                          norm_cap['xml'])
            error_count += 1
        else:
            logging.debug("Capacity value checked successfully against"
                          " virsh vol-dumpxml for volume: %s",
                          expected['name'])

        if abs(expected['capacity'] - norm_cap['qemu_img']) > delta_size:
            logging.error("Capacity mismatch for volume: %s against "
                          "qemu-img info\nExpected: %s\nActual: %s",
                          expected['name'], expected['capacity'],
                          norm_cap['qemu_img'])
            error_count += 1
        else:
            logging.debug("Capacity value checked successfully against"
                          " qemu-img info for volume: %s",
                          expected['name'])
        return error_count

    def get_all_secrets():
        """
        Return all exist libvirt secrets uuid in a list
        """
        secret_list = []
        secrets = virsh.secret_list().stdout.strip()
        for secret in secrets.splitlines()[2:]:
            secret_list.append(secret.strip().split()[0])
        return secret_list

    # Initialize the variables
    pool_name = params.get("pool_name")
    pool_type = params.get("pool_type")
    pool_target = params.get("pool_target")
    if os.path.dirname(pool_target) is "":
        pool_target = os.path.join(data_dir.get_tmp_dir(), pool_target)
    vol_name = params.get("volume_name")
    vol_number = int(params.get("number_of_volumes", "2"))
    capacity = params.get("volume_size", "1048576")
    allocation = params.get("volume_allocation", "1048576")
    vol_format = params.get("volume_format")
    source_name = params.get("gluster_source_name", "gluster-vol1")
    source_path = params.get("gluster_source_path", "/")
    encrypt_format = params.get("vol_encrypt_format")
    encrypt_secret = params.get("encrypt_secret")
    emulated_image = params.get("emulated_image")
    emulated_image_size = params.get("emulated_image_size")
    if not libvirt_version.version_compare(1, 0, 0):
        if pool_type == "gluster":
            test.cancel("Gluster pool is not supported in current"
                        " libvirt version.")

    try:
        str_capa = utils_misc.normalize_data_size(capacity, "B")
        int_capa = int(str(str_capa).split('.')[0])
    except ValueError:
        test.error("Translate size %s to 'B' failed" % capacity)
    try:
        str_capa = utils_misc.normalize_data_size(allocation, "B")
        int_allo = int(str(str_capa).split('.')[0])
    except ValueError:
        test.error("Translate size %s to 'B' failed" % allocation)

    # Stop multipathd to avoid start pool fail(For fs like pool, the new add
    # disk may in use by device-mapper, so start pool will report disk already
    # mounted error).
    multipathd = service.Factory.create_service("multipathd")
    multipathd_status = multipathd.status()
    if multipathd_status:
        multipathd.stop()

    # Get exists libvirt secrets before test
    ori_secrets = get_all_secrets()
    expected_vol = {}
    vol_type = 'file'
    if pool_type in ['disk', 'logical']:
        vol_type = 'block'
    if pool_type == 'gluster':
        vol_type = 'network'
    logging.debug("Debug:\npool_name:%s\npool_type:%s\npool_target:%s\n"
                  "vol_name:%s\nvol_number:%s\ncapacity:%s\nallocation:%s\n"
                  "vol_format:%s", pool_name, pool_type, pool_target,
                  vol_name, vol_number, capacity, allocation, vol_format)

    libv_pvt = utlv.PoolVolumeTest(test, params)
    # Run Testcase
    total_err_count = 0
    try:
        # Create a new pool
        params.update({"pool_target": pool_target, "source_name": source_name,
                       "source_path": source_path, "emulated_image": emulated_image})
        params.pop("image_size")
        libv_pvt.pre_pool(image_size=emulated_image_size, **params)
        for i in range(vol_number):
            volume_name = "%s_%d" % (vol_name, i)
            expected_vol['pool_name'] = pool_name
            expected_vol['pool_type'] = pool_type
            expected_vol['pool_target'] = pool_target
            expected_vol['capacity'] = int_capa
            expected_vol['allocation'] = int_allo
            expected_vol['format'] = vol_format
            expected_vol['name'] = volume_name
            expected_vol['type'] = vol_type
            expected_vol['encrypt_format'] = encrypt_format
            expected_vol['encrypt_secret'] = encrypt_secret
            # Creates volume
            if pool_type != "gluster":
                expected_vol['path'] = pool_target + '/' + volume_name
                new_volxml = vol_xml.VolXML()
                new_volxml.name = volume_name
                new_volxml.capacity = int_capa
                new_volxml.allocation = int_allo
                if vol_format:
                    new_volxml.format = vol_format
                encrypt_dict = {}
                if encrypt_format:
                    encrypt_dict.update({"format": encrypt_format})
                if encrypt_secret:
                    encrypt_dict.update({"secret": {'uuid': encrypt_secret}})
                if encrypt_dict:
                    new_volxml.encryption = new_volxml.new_encryption(**encrypt_dict)
                logging.debug("Volume XML for creation:\n%s", str(new_volxml))
                virsh.vol_create(pool_name, new_volxml.xml, debug=True)
            else:
                gluster_server_ip = params.get("gluster_server_ip")
                if gluster_server_ip:
                    ip_addr = gluster_server_ip
                else:
                    ip_addr = utils_net.get_host_ip_address()
                expected_vol['path'] = "gluster://%s/%s/%s" % (ip_addr,
                                                               source_name,
                                                               volume_name)
                process.run("qemu-img create -f %s %s %s" % (vol_format,
                                                             expected_vol['path'],
                                                             capacity), shell=True)
            virsh.pool_refresh(pool_name)
            # Check volumes
            total_err_count += check_vol(expected_vol)
            # Delete volume and check for results
            delete_volume(expected_vol)
            total_err_count += check_vol(expected_vol, False)
        if total_err_count > 0:
            test.fail("Get %s errors when checking volume" % total_err_count)
    finally:
        # Clean up
        for sec in get_all_secrets():
            if sec not in ori_secrets:
                virsh.secret_undefine(sec)
        try:
            libv_pvt.cleanup_pool(**params)
        except exceptions.TestFail as detail:
            logging.error(str(detail))
        if multipathd_status:
            multipathd.start()
