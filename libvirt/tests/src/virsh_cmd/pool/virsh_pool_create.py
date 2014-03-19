import os
import logging
from autotest.client.shared import error
from virttest import virsh, xml_utils, data_dir, libvirt_storage, libvirt_xml


def pool_check(pool_name, pool_ins):
    """
    Check the pool from given pool xml.
    """
    if not pool_ins.is_pool_active(pool_name):
        logging.debug("Can't find the active pool: %s", pool_name)
        return False
    logging.info(pool_name)
    pool_type = libvirt_xml.PoolXML.get_type(pool_name)
    pool_detail = libvirt_xml.PoolXML.get_pool_details(pool_name)
    logging.debug("Pool detail: %s", pool_detail)
    return True


def run(test, params, env):
    """
    Test command: virsh pool-create.

    Create a libvirt pool from an XML file.
    1) Use xml file from test parameters to create pool.
    2) Dump the exist pool to create pool.
    3) Negatively create pool(invalid option, no file, invalid file, duplicate
       pool name, and create pool under readonly mode).
    """

    pool_xml = params.get("pool_create_xml_file")
    pool_name = params.get("pool_create_name")
    option = params.get("pool_create_extra_option", "")
    use_exist_pool = "yes" == params.get("pool_create_use_exist_pool", "no")
    exist_pool_name = params.get("pool_create_exist_pool_name", "default")
    undefine_exist_pool = "yes" == params.get(
        "pool_create_undefine_exist_pool", "no")
    readonly_mode = "yes" == params.get("pool_create_readonly_mode", "no")
    status_error = "yes" == params.get("status_error", "no")
    exist_active = False

    # Deal with each parameters
    # backup the exist pool
    pool_ins = libvirt_storage.StoragePool()
    if use_exist_pool:
        if not pool_ins.pool_exists(exist_pool_name):
            raise error.TestFail("Require pool: %s exist", exist_pool_name)
        backup_xml = libvirt_xml.PoolXML.backup_xml(exist_pool_name)
        pool_xml = backup_xml
        exist_active = pool_ins.is_pool_active(exist_pool_name)

    # backup pool state
    pool_ins_state = virsh.pool_state_dict()
    logging.debug("Backed up pool(s): %s", pool_ins_state)

    if "--file" in option:
        pool_path = os.path.join(data_dir.get_data_dir(), 'images')
        dir_xml = """
<pool type='dir'>
  <name>%s</name>
  <target>
    <path>%s</path>
  </target>
</pool>
""" % (pool_name, pool_path)
        pool_xml = os.path.join(test.tmpdir, pool_xml)
        xml_object = open(pool_xml, 'w')
        xml_object.write(dir_xml)
        xml_object.close()

    # Delete the exist pool
    start_pool = False
    if undefine_exist_pool:
        poolxml = libvirt_xml.PoolXML.new_from_dumpxml(exist_pool_name)
        poolxml.name = pool_name
        pool_xml = poolxml.xml
        if pool_ins.is_pool_active(exist_pool_name):
            start_pool = True
        if not pool_ins.delete_pool(exist_pool_name):
            raise error.TestFail("Delete pool: %s fail", exist_pool_name)

    # Create an invalid pool xml file
    if pool_xml == "invalid-pool-xml":
        tmp_xml = xml_utils.TempXMLFile()
        tmp_xml.write('"<pool><<<BAD>>><\'XML</name\>'
                      '!@#$%^&*)>(}>}{CORRUPTE|>!</pool>')
        tmp_xml.flush()
        pool_xml = tmp_xml.name
        logging.info(pool_xml)

    # Readonly mode
    ro_flag = False
    if readonly_mode:
        logging.debug("Readonly mode test")
        ro_flag = True

    # Run virsh test
    if os.path.isfile(pool_xml):
        logging.debug("Create pool from file:\n %s", open(pool_xml, 'r').read())
    try:
        cmd_result = virsh.pool_create(
            pool_xml,
            option,
            ignore_status=True,
            debug=True,
            readonly=ro_flag)
        err = cmd_result.stderr.strip()
        status = cmd_result.exit_status
        if not status_error:
            if status:
                raise error.TestFail(err)
            elif not pool_check(pool_name, pool_ins):
                raise error.TestFail("Pool check fail")
        elif status_error and status == 0:
            # For an inactive 'default' pool, when the test runs to create
            # an existing pool of 'default', the test will pass because the
            # 'default' pool will be considered a transient pool when the
            # 'name' and 'uuid' match (which they do in this case). So
            # don't fail the test unnecessarily
            if (pool_name == exist_pool_name and not exist_active):
                pass
            else:
                raise error.TestFail("Expect fail, but run successfully.")
    finally:
        # Recover env
        # If we have a different pool name than default OR
        # we need to undefine this tests created default pool OR
        # we had a transient, active default pool created above, then
        # we need to destroy what the test created.
        # NB: When the active, transient pool is destroyed the
        # previously defined, but inactive pool will now exist.
        if pool_name != exist_pool_name or undefine_exist_pool or \
           (pool_name == exist_pool_name and not exist_active):
            virsh.pool_destroy(pool_name)

        # restore the undefined default pool
        if undefine_exist_pool:  # and not pool_ins.pool_exists(exist_pool_name):
            virsh.pool_define(backup_xml)
            if start_pool:
                pool_ins.start_pool(exist_pool_name)
            # Recover autostart
            if pool_ins_state[exist_pool_name]['autostart']:
                virsh.pool_autostart(exist_pool_name, ignore_status=False)
