import os
import logging

from avocado.utils import process

from virttest import virsh
from virttest import libvirt_storage
from virttest.utils_test import libvirt
from virttest import data_dir
from virttest import utils_package
from virttest import ceph
from virttest.libvirt_xml import pool_xml
from virttest.libvirt_xml import xcepts

from virttest import libvirt_version


def run(test, params, env):
    '''
    Test the command virsh pool-create-as

    (1) Prepare backend storage device
    (2) Define secret xml and set secret value
    (3) Test pool-create-as or virsh pool-define with authenication
    '''

    pool_options = params.get('pool_options', '')
    pool_name = params.get('pool_name')
    pool_type = params.get('pool_type')
    pool_target = params.get('pool_target', '')
    status_error = params.get('status_error') == "yes"

    # iscsi options
    emulated_size = params.get("iscsi_image_size", "1")
    iscsi_host = params.get("iscsi_host", "127.0.0.1")
    chap_user = params.get("iscsi_user")
    chap_passwd = params.get("iscsi_password")

    # ceph options
    ceph_auth_user = params.get("ceph_auth_user")
    ceph_auth_key = params.get("ceph_auth_key")
    ceph_host_ip = params.get("ceph_host_ip", "EXAMPLE_HOSTS")
    ceph_mon_ip = params.get("ceph_mon_ip", "EXAMPLE_MON_HOST")
    ceph_disk_name = params.get("ceph_disk_name", "EXAMPLE_SOURCE_NAME")
    ceph_client_name = params.get("ceph_client_name")
    ceph_client_key = params.get("ceph_client_key")
    key_file = os.path.join(data_dir.get_tmp_dir(), "ceph.key")
    key_opt = "--keyring %s" % key_file

    # auth options
    auth_usage = (params.get('auth_usage') == 'yes')
    auth_uuid = (params.get('auth_uuid') == 'yes')
    sec_ephemeral = params.get("secret_ephemeral", "no")
    sec_private = params.get("secret_private", "yes")
    sec_desc = params.get("secret_description")
    auth_type = params.get("auth_type")
    sec_usage = params.get("secret_usage_type")
    sec_target = params.get("secret_usage_target")
    sec_name = params.get("secret_name")
    auth_sec_dict = {"sec_ephemeral": sec_ephemeral,
                     "sec_private": sec_private,
                     "sec_desc": sec_desc,
                     "sec_usage": sec_usage,
                     "sec_target": sec_target,
                     "sec_name": sec_name}

    if sec_usage == "iscsi":
        auth_username = chap_user
        sec_password = chap_passwd
        secret_usage = sec_target

    if sec_usage == "ceph":
        auth_username = ceph_auth_user
        sec_password = ceph_auth_key
        secret_usage = sec_name

    if pool_target and not os.path.isdir(pool_target):
        if os.path.isfile(pool_target):
            logging.error('<target> must be a directory')
        else:
            os.makedirs(pool_target)

    def setup_ceph_auth():
        disk_path = ("rbd:%s:mon_host=%s" % (ceph_disk_name, ceph_mon_ip))
        disk_path += (":id=%s:key=%s" % (ceph_auth_user, ceph_auth_key))

        if not utils_package.package_install(["ceph-common"]):
            test.error("Failed to install ceph-common")

        with open(key_file, 'w') as f:
            f.write("[%s]\n\tkey = %s\n" %
                    (ceph_client_name, ceph_client_key))

        # Delete the disk if it exists
        cmd = ("rbd -m {0} {1} info {2} && rbd -m {0} {1} rm "
               "{2}".format(ceph_mon_ip, key_opt, ceph_disk_name))
        process.run(cmd, ignore_status=True, shell=True)

        # Create an local image and make FS on it.
        img_file = os.path.join(data_dir.get_tmp_dir(), "test.img")
        disk_cmd = ("qemu-img create -f raw {0} 10M && mkfs.ext4 -F {0}"
                    .format(img_file))
        process.run(disk_cmd, ignore_status=False, shell=True)

        # Convert the image to remote storage
        # Ceph can only support raw format
        disk_cmd = ("qemu-img convert -O %s %s %s"
                    % ("raw", img_file, disk_path))
        process.run(disk_cmd, ignore_status=False, shell=True)

    def setup_iscsi_auth():
        iscsi_target, lun_num = libvirt.setup_or_cleanup_iscsi(is_setup=True,
                                                               is_login=False,
                                                               image_size=emulated_size,
                                                               chap_user=chap_user,
                                                               chap_passwd=chap_passwd)
        return iscsi_target

    def check_auth_in_xml(dparams):
        sourcexml = pool_xml.PoolXML.new_from_dumpxml(pool_name).get_source()
        with open(sourcexml.xml) as xml_f:
            logging.debug("Source XML is: \n%s", xml_f.read())

        # Check result
        try:
            for name, v_expect in dparams.items():
                if v_expect != sourcexml[name]:
                    test.fail("Expect to find %s=%s, but got %s=%s"
                              % (name, v_expect, name, sourcexml[name]))
        except xcepts.LibvirtXMLNotFoundError as details:
            if "usage not found" in str(details) and auth_uuid:
                pass  # Not a auth_usage test
            elif "uuid not found" in str(details) and auth_usage:
                pass  # Not a auth_uuid test
            else:
                test.fail(details)

    def check_result(result, expect_error=False):
        # pool-define-as return CmdResult
        if isinstance(result, process.CmdResult):
            result = (result.exit_status == 0)  # True means run success

        if expect_error:
            if result:
                test.fail("Expect to fail but run success")
        elif not expect_error:
            if not result:
                test.fail("Expect to succeed but run failure")
        else:
            logging.info("It's an expected error")

    if not libvirt_version.version_compare(3, 9, 0):
        test.cancel("Pool create/define with authentication"
                    " not support in this libvirt version")

    sec_uuid = ""
    img_file = ""
    # Prepare a blank params to confirm if delete the configure at the end of the test
    ceph_cfg = ""
    libvirt_pool = libvirt_storage.StoragePool()
    try:
        # Create secret xml and set value
        encode = True
        if sec_usage == "ceph":
            encode = False  # Ceph key already encoded
        sec_uuid = libvirt.create_secret(auth_sec_dict)
        virsh.secret_set_value(sec_uuid, sec_password, encode=encode, debug=True)

        if sec_usage == "iscsi":
            iscsi_dev = setup_iscsi_auth()
            pool_options += (" --source-host %s --source-dev %s"
                             " --auth-type %s --auth-username %s"
                             % (iscsi_host, iscsi_dev, auth_type, auth_username))

        if sec_usage == "ceph":
            # Create config file if it doesn't exist
            ceph_cfg = ceph.create_config_file(ceph_mon_ip)
            setup_ceph_auth()
            rbd_pool = ceph_disk_name.split('/')[0]
            pool_options += (" --source-host %s --source-name %s"
                             " --auth-type %s --auth-username %s"
                             % (ceph_host_ip, rbd_pool, auth_type, auth_username))

        if auth_usage:
            pool_options += " --secret-usage %s" % secret_usage

        if auth_uuid:
            pool_options += " --secret-uuid %s" % sec_uuid

        # Run test cases
        func_name = params.get("test_func", "pool_create_as")
        logging.info('Perform test runner: %s', func_name)
        if func_name == "pool_create_as":
            func = virsh.pool_create_as
        if func_name == "pool_define_as":
            func = virsh.pool_define_as
        result = func(pool_name, pool_type, pool_target,
                      extra=pool_options, debug=True)

        # Check status_error
        check_result(result, expect_error=status_error)
        if not status_error:
            # Check pool status
            pool_status = libvirt_pool.pool_state(pool_name)
            if ((pool_status == 'inactive' and func_name == "pool_define_as") or
                    (pool_status == "active" and func_name == "pool_create_as")):
                logging.info("Expected pool status:%s" % pool_status)
            else:
                test.fail("Not an expected pool status: %s" % pool_status)
            # Check pool dumpxml
            dict_expect = {"auth_type": auth_type, "auth_username": auth_username,
                           "secret_usage": secret_usage, "secret_uuid": sec_uuid}
            check_auth_in_xml(dict_expect)
    finally:
        # Clean up
        logging.info("Start to cleanup")
        # Remove ceph configure file if created.
        if ceph_cfg:
            os.remove(ceph_cfg)
        if os.path.exists(img_file):
            os.remove(img_file)
        virsh.secret_undefine(sec_uuid, ignore_status=True)
        libvirt.setup_or_cleanup_iscsi(is_setup=False)
        if libvirt_pool.pool_exists(pool_name):
            libvirt_pool.delete_pool(pool_name)
