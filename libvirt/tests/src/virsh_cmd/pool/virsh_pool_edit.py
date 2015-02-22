import os
import logging
from autotest.client.shared import error
from virttest import virsh
from virttest import libvirt_storage
from virttest import data_dir
from virttest import remote
from virttest import aexpect
from virttest.libvirt_xml import pool_xml
from virttest.utils_test import libvirt


def edit_pool(pool, edit_cmd):
    """
    Edit libvirt storage pool.

    :param pool: pool name or uuid.
    :param edit_cmd : edit commad line.
    """
    session = aexpect.ShellSession("sudo -s")
    try:
        session.sendline("virsh pool-edit %s" % pool)
        logging.info("edit_cmd: %s", edit_cmd)
        for cmd in edit_cmd:
            logging.info("cmd: %s", cmd)
            session.sendline(cmd)
        session.send('\x1b')
        session.send('ZZ')
        remote.handle_prompts(session, None, None, r"[\#\$]\s*$")
        session.close()
        logging.info("Succeed to do pool edit.")
    except (aexpect.ShellError, aexpect.ExpectError), details:
        log = session.get_output()
        session.close()
        raise error.TestFail("Failed to do pool edit: %s\n%s"
                             % (details, log))


def check_pool(pool_name, check_point, expect_value=""):
    """
    Check the pool after edit it.

    :param pool_name: Name of the pool.
    :param check_point: Which part for checking.
    :param expect_value: New value for update.
    :return: True if check pass or false if check fail.
    """
    try:
        new_poolxml = pool_xml.PoolXML.new_from_dumpxml(pool_name)
        logging.debug("After edit pool:")
        new_poolxml.debug_xml()
        if check_point == "pool_target_path":
            actual_value = new_poolxml.target_path
        elif check_point == "pool_format_type":
            actual_value = new_poolxml.source.format_type
        elif check_point == "pool_redefine":
            # No exception means pool_name exist
            return True
        else:
            logging.error("Unsupport check point %s", check_point)
            return False
    except Exception, e:
        logging.error("Error occured: %s", e)
        return False
    if expect_value == actual_value:
        logging.debug("Edit pool check pass")
        return True
    else:
        logging.error("Edit pool check not pass")
        return False


def run(test, params, env):
    """
    Test command: virsh pool-edit.

    Edit the XML configuration for a storage pool('dir' type as default).
    1) Edit pool by different methods.
    2) Check the edit result and cleanup env.
    """

    pool_ref = params.get("pool_ref", "name")
    pool_name = params.get("pool_name", "default")
    pool_uuid = params.get("pool_uuid", "")
    pool_exist = "yes" == params.get("pool_exist", "yes")
    status_error = "yes" == params.get("status_error", "no")
    pool_type = params.get("pool_type", "dir")
    pool_target = os.path.join(data_dir.get_tmp_dir(),
                               params.get("pool_target", "pool_target"))
    source_name = params.get("pool_source_name", "gluster-vol1")
    source_path = params.get("pool_source_path", "/")
    emulated_image = params.get("emulated_image", "emulated_image_disk")
    edit_target = params.get("edit_target", "target_path")
    redefine_pool_flag = False
    pool = pool_name
    if pool_ref == "uuid":
        pool = pool_uuid
    poolxml = pool_xml.PoolXML()
    libvirt_pool = libvirt_storage.StoragePool()
    poolvolune_test = libvirt.PoolVolumeTest(test, params)
    check_pool_name = pool_name
    new_path = ""
    try:
        if pool_exist and not status_error:
            if libvirt_pool.pool_exists(pool_name):
                logging.debug("Find pool '%s' to edit.", pool_name)
                redefine_pool_flag = True
            else:
                logging.debug("Define pool '%s' as it not exist", pool_name)
                if pool_type == "gluster":
                    poolvolune_test.pre_pool(pool_name, pool_type, pool_target,
                                             emulated_image,
                                             source_name=source_name,
                                             source_path=source_path)
                else:
                    poolvolune_test.pre_pool(pool_name, pool_type, pool_target,
                                             emulated_image)
            if not pool_uuid and pool_ref == "uuid":
                pool = libvirt_pool.get_pool_uuid(pool_name)
            poolxml.xml = pool_xml.PoolXML().new_from_dumpxml(pool_name).xml
            logging.debug("Before edit pool:")
            poolxml.debug_xml()

            expect_value = ""
            # Test: Edit target path
            if edit_target == "pool_target_path":
                edit_cmd = []
                new_path = os.path.join(data_dir.get_tmp_dir(), "new_path")
                os.mkdir(new_path)
                edit_cmd.append(":%s/<path>.*</<path>" +
                                new_path.replace('/', '\/') + "<")
                pool_target = new_path
                expect_value = new_path
            # Test: Edit disk pool format type:
            elif edit_target == "pool_format_type":
                edit_cmd = []
                new_format_type = params.get("pool_format", "dos")
                edit_cmd.append(":%s/<format type=.*\/>/<format type='" +
                                new_format_type + "'\/>/")
                expect_value = new_format_type
            # Test: Refine(Delete uuid, edit pool name and target path)
            elif edit_target == "pool_redefine":
                edit_cmd = []
                new_pool_name = params.get("new_pool_name", "new_edit_pool")
                edit_cmd.append(":g/<uuid>/d")
                new_path = os.path.join(data_dir.get_tmp_dir(), "new_pool")
                os.mkdir(new_path)
                edit_cmd.append(":%s/<path>.*</<path>" +
                                new_path.replace('/', '\/') + "<")
                edit_cmd.append(":%s/<name>" + pool_name + "</<name>" +
                                new_pool_name + "<")
                pool_target = new_path
                check_pool_name = new_pool_name

            else:
                raise error.TestNAError("No edit method for %s" % edit_target)

            # run test and check the result
            logging.info("pool=%s", pool)
            edit_pool(pool, edit_cmd)
            if libvirt_pool.is_pool_active(pool_name):
                libvirt_pool.destroy_pool(pool_name)
            if not libvirt_pool.start_pool(check_pool_name):
                raise error.TestFail("Fail to start pool after edit it.")
            if not check_pool(check_pool_name, edit_target, expect_value):
                raise error.TestFail("Edit pool fail")
        elif not pool_exist and not status_error:
            raise error.TestFail("Conflict condition: pool not exist and expect "
                                 "pool edit succeed.")
        else:
            # negative test
            output = virsh.pool_edit(pool)
            if output.exit_status:
                logging.info("Fail to do pool edit as expect: %s",
                             output.stderr.strip())
            else:
                redefine_pool_flag = True
                raise error.TestFail("Expect fail but do pool edit succeed.")
    finally:
        for pool in [pool_name, check_pool_name]:
            if libvirt_pool.pool_exists(pool):
                poolvolune_test.cleanup_pool(check_pool_name, pool_type,
                                             pool_target, emulated_image,
                                             source_name=source_name)
        if redefine_pool_flag:
            try:
                # poolxml could be empty if error happened when define pool
                poolxml.pool_define()
            finally:
                logging.error("Recover pool %s failed", pool_name)
        if os.path.exists(new_path):
            os.rmdir(new_path)
