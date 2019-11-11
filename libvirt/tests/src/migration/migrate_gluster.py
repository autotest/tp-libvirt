import logging
import os
import re


from virttest import libvirt_vm
from virttest import virsh
from virttest import remote
from virttest import utils_misc
from virttest import utils_package
from virttest import utils_conn
from virttest import gluster

from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml
from virttest.compat_52lts import results_stdout_52lts
from virttest.compat_52lts import results_stderr_52lts


def check_parameters(test, params):
    """
    Make sure all of parameters are assigned a valid value

    :param test: the test object
    :param params: the parameters to be checked

    :raise: test.cancel if invalid value exists
    """
    migrate_dest_host = params.get("migrate_dest_host")
    migrate_dest_pwd = params.get("migrate_dest_pwd")
    migrate_source_host = params.get("migrate_source_host")
    migrate_source_pwd = params.get("migrate_source_pwd")

    args_list = [migrate_dest_host,
                 migrate_dest_pwd, migrate_source_host,
                 migrate_source_pwd]

    for arg in args_list:
        if arg and arg.count("EXAMPLE"):
            test.cancel("Please assign a value for %s!" % arg)


def run(test, params, env):
    """
    Test migration with glusterfs.
    """
    def create_or_clean_backend_dir(g_uri, params, session=None,
                                    is_clean=False):
        """
        Create/cleanup backend directory

        :params g_uri: glusterfs uri
        :params params: the parameters to be checked
        :params session: VM/remote session object
        :params is_cleanup: True for cleanup backend directory;
                            False for create one.
        :return: gluster_img if is_clean is equal to True
        """
        mount_point = params.get("gluster_mount_dir")
        is_symlink = params.get("gluster_create_symlink") == "yes"
        symlink_name = params.get("gluster_symlink")
        gluster_img = None
        if not is_clean:
            if not utils_misc.check_exists(mount_point, session):
                utils_misc.make_dirs(mount_point, session)

            if gluster.glusterfs_is_mounted(mount_point, session):
                gluster.glusterfs_umount(g_uri, mount_point, session)
            gluster.glusterfs_mount(g_uri, mount_point, session)

            gluster_img = os.path.join(mount_point, disk_img)
            if is_symlink:
                utils_misc.make_symlink(mount_point, symlink_name)
                utils_misc.make_symlink(mount_point, symlink_name, remote_session)
                gluster_img = os.path.join(symlink_name, disk_img)
            return gluster_img
        else:
            if is_symlink:
                utils_misc.rm_link(symlink_name, session)

            gluster.glusterfs_umount(g_uri, mount_point, session)
            if utils_misc.check_exists(mount_point, session):
                utils_misc.safe_rmdir(gluster_mount_dir, session=session)

    def do_migration(vm, dest_uri, options, extra):
        """
        Execute the migration with given parameters

        :param vm: the guest to be migrated
        :param dest_uri: the destination uri for migration
        :param options: options next to 'migrate' command
        :param extra: options in the end of the migrate command line

        :return: CmdResult object
        """
        # Migrate the guest.
        virsh_args.update({"ignore_status": True})
        migration_res = vm.migrate(dest_uri, options, extra, **virsh_args)
        if int(migration_res.exit_status) != 0:
            logging.error("Migration failed for %s.", vm_name)
            return migration_res

        if vm.is_alive():
            logging.info("VM is alive on destination %s.", dest_uri)
        else:
            test.fail("VM is not alive on destination %s" % dest_uri)

        # Throws exception if console shows panic message
        vm.verify_kernel_crash()
        return migration_res

    def check_migration_res(result):
        """
        Check if the migration result is as expected

        :param result: the output of migration
        :raise: test.fail if test is failed
        """
        if not result:
            test.error("No migration result is returned.")
        logging.info("Migration out: %s", results_stdout_52lts(result).strip())
        logging.info("Migration error: %s", results_stderr_52lts(result).strip())

        if status_error:  # Migration should fail
            if err_msg:   # Special error messages are expected
                if not re.search(err_msg, results_stderr_52lts(result).strip()):
                    test.fail("Can not find the expected patterns '%s' in "
                              "output '%s'" % (err_msg,
                                               results_stderr_52lts(result).strip()))
                else:
                    logging.debug("It is the expected error message")
            else:
                if int(result.exit_status) != 0:
                    logging.debug("Migration failure is expected result")
                else:
                    test.fail("Migration success is unexpected result")
        else:
            if int(result.exit_status) != 0:
                test.fail(results_stderr_52lts(result).strip())

    # Local variables
    virsh_args = {"debug": True}
    server_ip = params["server_ip"] = params.get("remote_ip")
    server_user = params["server_user"] = params.get("remote_user", "root")
    server_pwd = params["server_pwd"] = params.get("remote_pwd")
    client_ip = params["client_ip"] = params.get("local_ip")
    client_pwd = params["client_pwd"] = params.get("local_pwd")
    extra = params.get("virsh_migrate_extra")
    options = params.get("virsh_migrate_options")
    virsh_options = params.get("virsh_options", "--verbose --live")

    vol_name = params.get("vol_name")
    disk_format = params.get("disk_format", "qcow2")
    gluster_mount_dir = params.get("gluster_mount_dir")

    status_error = "yes" == params.get("status_error", "no")
    err_msg = params.get("err_msg")
    host_ip = params.get("gluster_server_ip", "")
    migr_vm_back = params.get("migrate_vm_back", "no") == "yes"

    selinux_local = params.get('set_sebool_local', 'yes') == "yes"
    selinux_remote = params.get('set_sebool_remote', 'no') == "yes"
    sebool_fusefs_local = params.get('set_sebool_fusefs_local', 'yes')
    sebool_fusefs_remote = params.get('set_sebool_fusefs_remote', 'yes')
    test_dict = dict(params)
    test_dict["local_boolean_varible"] = "virt_use_fusefs"
    test_dict["remote_boolean_varible"] = "virt_use_fusefs"

    remove_pkg = False
    seLinuxBool = None
    seLinuxfusefs = None
    gluster_uri = None
    mig_result = None

    # Make sure all of parameters are assigned a valid value
    check_parameters(test, params)

    # params for migration connection
    params["virsh_migrate_desturi"] = libvirt_vm.complete_uri(
                                       params.get("migrate_dest_host"))
    params["virsh_migrate_connect_uri"] = libvirt_vm.complete_uri(
                                       params.get("migrate_source_host"))
    src_uri = params.get("virsh_migrate_connect_uri")
    dest_uri = params.get("virsh_migrate_desturi")

    # For --postcopy enable
    postcopy_options = params.get("postcopy_options")
    if postcopy_options:
        virsh_options = "%s %s" % (virsh_options, postcopy_options)
        params['virsh_options'] = virsh_options

    vm_name = params.get("migrate_main_vm")
    vm = env.get_vm(vm_name)
    vm.verify_alive()

    # Back up xml file.
    new_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    orig_config_xml = new_xml.copy()

    migrate_setup = libvirt.MigrationTest()
    try:
        # Create a remote runner for later use
        runner_on_target = remote.RemoteRunner(host=server_ip,
                                               username=server_user,
                                               password=server_pwd)

        # Configure selinux
        if selinux_local or selinux_remote:
            seLinuxBool = utils_misc.SELinuxBoolean(params)
            seLinuxBool.setup()
            if sebool_fusefs_local or sebool_fusefs_remote:
                seLinuxfusefs = utils_misc.SELinuxBoolean(test_dict)
                seLinuxfusefs.setup()

        # Setup glusterfs and disk xml.
        disk_img = "gluster.%s" % disk_format
        params['disk_img'] = disk_img
        libvirt.set_vm_disk(vm, params)

        vm_xml_cxt = virsh.dumpxml(vm_name).stdout_text.strip()
        logging.debug("The VM XML with gluster disk source: \n%s", vm_xml_cxt)

        # Check if gluster server is deployed locally
        if not host_ip:
            logging.debug("Enable port 24007 and 49152:49216")
            migrate_setup.migrate_pre_setup(src_uri, params, ports="24007")
            migrate_setup.migrate_pre_setup(src_uri, params)
            gluster_uri = "{}:{}".format(client_ip, vol_name)
        else:
            gluster_uri = "{}:{}".format(host_ip, vol_name)

        remote_session = remote.wait_for_login('ssh', server_ip, '22',
                                               server_user, server_pwd,
                                               r"[\#\$]\s*$")

        if gluster_mount_dir:
            # The package 'glusterfs-fuse' is not installed on target
            # which makes issue when trying to 'mount -t glusterfs'
            pkg_name = 'glusterfs-fuse'
            logging.debug("Check if glusterfs-fuse is installed")
            pkg_mgr = utils_package.package_manager(remote_session, pkg_name)
            if not pkg_mgr.is_installed(pkg_name):
                logging.debug("glusterfs-fuse will be installed")
                if not pkg_mgr.install():
                    test.error("Package '%s' installation fails" % pkg_name)
                else:
                    remove_pkg = True

            gluster_img = create_or_clean_backend_dir(gluster_uri, params)
            create_or_clean_backend_dir(gluster_uri, params, remote_session)

            logging.debug("Gluster Image is %s", gluster_img)
            gluster_backend_disk = {'disk_source_name': gluster_img}
            # Update disk xml with gluster image in backend dir
            libvirt.set_vm_disk(vm, gluster_backend_disk)
        remote_session.close()

        mig_result = do_migration(vm, dest_uri, options, extra)
        check_migration_res(mig_result)

        if migr_vm_back:
            ssh_connection = utils_conn.SSHConnection(server_ip=client_ip,
                                                      server_pwd=client_pwd,
                                                      client_ip=server_ip,
                                                      client_pwd=server_pwd)
            try:
                ssh_connection.conn_check()
            except utils_conn.ConnectionError:
                ssh_connection.conn_setup()
                ssh_connection.conn_check()

            # Pre migration setup for local machine
            migrate_setup.migrate_pre_setup(src_uri, params)
            cmd = "virsh migrate %s %s %s" % (vm_name,
                                              virsh_options, src_uri)
            logging.debug("Start migrating: %s", cmd)
            cmd_result = remote.run_remote_cmd(cmd, params, runner_on_target)
            logging.info(cmd_result)

            if cmd_result.exit_status:
                destroy_cmd = "virsh destroy %s" % vm_name
                remote.run_remote_cmd(destroy_cmd, params, runner_on_target,
                                      ignore_status=False)
                test.fail("Failed to run '%s' on remote: %s"
                          % (cmd, cmd_result))

    finally:
        logging.info("Recovery test environment")
        orig_config_xml.sync()

        # Clean up of pre migration setup for local machine
        if migr_vm_back:
            if 'ssh_connection' in locals():
                ssh_connection.auto_recover = True
            migrate_setup.migrate_pre_setup(src_uri, params,
                                            cleanup=True)

        # Cleanup selinu configuration
        if seLinuxBool:
            seLinuxBool.cleanup()
            if seLinuxfusefs:
                seLinuxfusefs.cleanup()

        # Disable ports 24007 and 49152:49216
        if not host_ip:
            logging.debug("Disable 24007 and 49152:49216 in Firewall")
            migrate_setup.migrate_pre_setup(src_uri, params,
                                            cleanup=True, ports="24007")
            migrate_setup.migrate_pre_setup(src_uri, params,
                                            cleanup=True)

        gluster.setup_or_cleanup_gluster(False, **params)

        # Cleanup backend directory/symlink
        if gluster_mount_dir and gluster_uri:
            remote_session = remote.wait_for_login('ssh', server_ip, '22',
                                                   server_user, server_pwd,
                                                   r"[\#\$]\s*$")
            create_or_clean_backend_dir(gluster_uri, params, is_clean=True)
            create_or_clean_backend_dir(gluster_uri, params, remote_session,
                                        True)
            if remove_pkg:
                pkg_mgr = utils_package.package_manager(remote_session,
                                                        pkg_name)
                if pkg_mgr.is_installed(pkg_name):
                    logging.debug("glusterfs-fuse will be uninstalled")
                    if not pkg_mgr.remove():
                        logging.error("Package '%s' un-installation fails", pkg_name)
            remote_session.close()
