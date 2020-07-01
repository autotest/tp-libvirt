import re
import os
import logging

from avocado.utils import process

from virttest import libvirt_storage
from virttest import data_dir
from virttest import virsh
from virttest.staging import lv_utils
from virttest.utils_test import libvirt as utlv

from virttest import libvirt_version


def run(test, params, env):
    """
    Test the virsh pool commands with acl, initiate a pool then do
    following operations.

    (1) Undefine a given type pool
    (2) Define the pool from xml
    (3) Build given type pool
    (4) Start pool
    (5) Destroy pool
    (6) Refresh pool after start it
    (7) Run vol-list with the pool
    (9) Delete pool

    For negative cases, redo failed step to make the case run continue.
    Run cleanup at last restore env.
    """

    # Initialize the variables
    pool_name = params.get("pool_name", "temp_pool_1")
    pool_type = params.get("pool_type", "dir")
    pool_target = params.get("pool_target", "")
    # The file for dumped pool xml
    pool_xml = os.path.join(data_dir.get_tmp_dir(), "pool.xml.tmp")
    if os.path.dirname(pool_target) is "":
        pool_target = os.path.join(data_dir.get_tmp_dir(), pool_target)
    vol_name = params.get("vol_name", "temp_vol_1")
    # Use pool name as VG name
    vg_name = pool_name
    vol_path = os.path.join(pool_target, vol_name)
    define_acl = "yes" == params.get("define_acl", "no")
    undefine_acl = "yes" == params.get("undefine_acl", "no")
    start_acl = "yes" == params.get("start_acl", "no")
    destroy_acl = "yes" == params.get("destroy_acl", "no")
    build_acl = "yes" == params.get("build_acl", "no")
    delete_acl = "yes" == params.get("delete_acl", "no")
    refresh_acl = "yes" == params.get("refresh_acl", "no")
    vol_list_acl = "yes" == params.get("vol_list_acl", "no")
    list_dumpxml_acl = "yes" == params.get("list_dumpxml_acl", "no")
    src_pool_error = "yes" == params.get("src_pool_error", "no")
    define_error = "yes" == params.get("define_error", "no")
    undefine_error = "yes" == params.get("undefine_error", "no")
    start_error = "yes" == params.get("start_error", "no")
    destroy_error = "yes" == params.get("destroy_error", "no")
    build_error = "yes" == params.get("build_error", "no")
    delete_error = "yes" == params.get("delete_error", "no")
    refresh_error = "yes" == params.get("refresh_error", "no")
    vol_list_error = "yes" == params.get("vol_list_error", "no")
    # Clean up flags:
    # cleanup_env[0] for nfs, cleanup_env[1] for iscsi, cleanup_env[2] for lvm
    # cleanup_env[3] for selinux backup status, cleanup_env[4] for gluster
    cleanup_env = [False, False, False, "", False]
    # libvirt acl related params
    uri = params.get("virsh_uri")
    unprivileged_user = params.get('unprivileged_user')
    if unprivileged_user:
        if unprivileged_user.count('EXAMPLE'):
            unprivileged_user = 'testacl'

    if not libvirt_version.version_compare(1, 1, 1):
        if params.get('setup_libvirt_polkit') == 'yes':
            test.cancel("API acl test not supported in current"
                        " libvirt version.")

    acl_dargs = {'uri': uri, 'unprivileged_user': unprivileged_user,
                 'debug': True}

    def check_pool_list(pool_name, option="--all", expect_error=False):
        """
        Check pool by running pool-list command with given option.

        :param pool_name: Name of the pool
        :param option: option for pool-list command
        :param expect_error: Boolean value, expect command success or fail
        """
        found = False
        # Get the list stored in a variable
        if list_dumpxml_acl:
            result = virsh.pool_list(option, **acl_dargs)
        else:
            result = virsh.pool_list(option, ignore_status=True)
        utlv.check_exit_status(result, False)
        output = re.findall(r"(\S+)\ +(\S+)\ +(\S+)",
                            str(result.stdout.strip()))
        for item in output:
            if pool_name in item[0]:
                found = True
                break
        if found:
            logging.debug("Find pool '%s' in pool list.", pool_name)
        else:
            logging.debug("Not find pool %s in pool list.", pool_name)
        if expect_error and found:
            test.fail("Unexpect pool '%s' exist." % pool_name)
        if not expect_error and not found:
            test.fail("Expect pool '%s' doesn't exist." % pool_name)

    # Run Testcase
    kwargs = {'source_format': params.get('pool_source_format', 'ext4')}
    try:
        _pool = libvirt_storage.StoragePool()
        # Init a pool for test
        result = utlv.define_pool(pool_name, pool_type, pool_target,
                                  cleanup_env, **kwargs)
        utlv.check_exit_status(result, src_pool_error)
        option = "--inactive --type %s" % pool_type
        check_pool_list(pool_name, option)

        if list_dumpxml_acl:
            xml = virsh.pool_dumpxml(pool_name, to_file=pool_xml, **acl_dargs)
        else:
            xml = virsh.pool_dumpxml(pool_name, to_file=pool_xml)
        logging.debug("Pool '%s' XML:\n%s", pool_name, xml)

        # Step (1)
        # Undefine pool
        if undefine_acl:
            result = virsh.pool_undefine(pool_name, **acl_dargs)
        else:
            result = virsh.pool_undefine(pool_name, ignore_status=True)
        utlv.check_exit_status(result, undefine_error)
        if undefine_error:
            check_pool_list(pool_name, "--all", False)
            # Redo under negative case to keep case continue
            result = virsh.pool_undefine(pool_name, ignore_status=True)
            utlv.check_exit_status(result)
            check_pool_list(pool_name, "--all", True)
        else:
            check_pool_list(pool_name, "--all", True)

        # Step (2)
        # Define pool from XML file
        if define_acl:
            result = virsh.pool_define(pool_xml, **acl_dargs)
        else:
            result = virsh.pool_define(pool_xml)
        utlv.check_exit_status(result, define_error)
        if define_error:
            # Redo under negative case to keep case continue
            result = virsh.pool_define(pool_xml)
            utlv.check_exit_status(result)

        # Step (3)
        # '--overwrite/--no-overwrite' just for fs/disk/logiacl type pool
        # disk/fs pool: as prepare step already make label and create filesystem
        #               for the disk, use '--overwrite' is necessary
        # logical_pool: build pool will fail if VG already exist, BZ#1373711
        if pool_type != "logical":
            option = ''
            if pool_type in ['disk', 'fs']:
                option = '--overwrite'
            result = virsh.pool_build(pool_name, option, ignore_status=True)
            utlv.check_exit_status(result)
            if build_acl:
                result = virsh.pool_build(pool_name, option, **acl_dargs)
            else:
                result = virsh.pool_build(pool_name, option,
                                          ignore_status=True)
            utlv.check_exit_status(result, build_error)
        if build_error:
            # Redo under negative case to keep case continue
            result = virsh.pool_build(pool_name, option,
                                      ignore_status=True)
            utlv.check_exit_status(result)

        # For iSCSI pool, we need discover targets before start the pool
        if pool_type == 'iscsi':
            cmd = 'iscsiadm -m discovery -t sendtargets -p 127.0.0.1'
            process.run(cmd, shell=True)

        # Step (4)
        # Pool start
        if start_acl:
            result = virsh.pool_start(pool_name, **acl_dargs)
        else:
            result = virsh.pool_start(pool_name, ignore_status=True)
        utlv.check_exit_status(result, start_error)
        if start_error:
            # Redo under negative case to keep case continue
            result = virsh.pool_start(pool_name, ignore_status=True)
            utlv.check_exit_status(result)

        option = "--persistent --type %s" % pool_type
        check_pool_list(pool_name, option)

        # Step (5)
        # Pool destroy
        if destroy_acl:
            result = virsh.pool_destroy(pool_name, **acl_dargs)
        else:
            result = virsh.pool_destroy(pool_name)
        if result:
            if destroy_error:
                test.fail("Expect fail, but run successfully.")
        else:
            if not destroy_error:
                test.fail("Pool %s destroy failed, not expected."
                          % pool_name)
            else:
                # Redo under negative case to keep case continue
                if virsh.pool_destroy(pool_name):
                    logging.debug("Pool %s destroyed.", pool_name)
                else:
                    test.fail("Destroy pool % failed." % pool_name)

        # Step (6)
        # Pool refresh for 'dir' type pool
        # Pool start
        result = virsh.pool_start(pool_name, ignore_status=True)
        utlv.check_exit_status(result)
        if pool_type == "dir":
            os.mknod(vol_path)
            if refresh_acl:
                result = virsh.pool_refresh(pool_name, **acl_dargs)
            else:
                result = virsh.pool_refresh(pool_name)
            utlv.check_exit_status(result, refresh_error)

        # Step (7)
        # Pool vol-list
        if vol_list_acl:
            result = virsh.vol_list(pool_name, **acl_dargs)
        else:
            result = virsh.vol_list(pool_name)
        utlv.check_exit_status(result, vol_list_error)

        # Step (8)
        # Pool delete for 'dir' type pool
        if virsh.pool_destroy(pool_name):
            logging.debug("Pool %s destroyed.", pool_name)
        else:
            test.fail("Destroy pool % failed." % pool_name)
        if pool_type == "dir":
            if os.path.exists(vol_path):
                os.remove(vol_path)
            if delete_acl:
                result = virsh.pool_delete(pool_name, **acl_dargs)
            else:
                result = virsh.pool_delete(pool_name, ignore_status=True)
            utlv.check_exit_status(result, delete_error)
            option = "--inactive --type %s" % pool_type
            check_pool_list(pool_name, option)
            if not delete_error:
                if os.path.exists(pool_target):
                    test.fail("The target path '%s' still exist." %
                              pool_target)

        result = virsh.pool_undefine(pool_name, ignore_status=True)
        utlv.check_exit_status(result)
        check_pool_list(pool_name, "--all", True)
    finally:
        # Clean up
        if os.path.exists(pool_xml):
            os.remove(pool_xml)
        if not _pool.delete_pool(pool_name):
            logging.error("Can't delete pool: %s", pool_name)
        if cleanup_env[2]:
            cmd = "pvs |grep %s |awk '{print $1}'" % vg_name
            pv_name = process.run(cmd, shell=True).stdout_text
            lv_utils.vg_remove(vg_name)
            process.run("pvremove %s" % pv_name, shell=True)
        if cleanup_env[1]:
            utlv.setup_or_cleanup_iscsi(False)
        if cleanup_env[0]:
            utlv.setup_or_cleanup_nfs(
                False, restore_selinux=cleanup_env[3])
