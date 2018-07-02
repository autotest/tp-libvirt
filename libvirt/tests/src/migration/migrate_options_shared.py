import os
import logging
import time
import re
import threading

from avocado.utils import process
from avocado.utils import memory
from avocado.core import exceptions

from virttest import libvirt_vm
from virttest import utils_test
from virttest import defaults
from virttest import virsh
from virttest import remote
from virttest import utils_package

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.utils_conn import TLSConnection
from virttest.compat_52lts import results_stdout_52lts, results_stderr_52lts


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


def setup_libvirtd_conf_dict(params):
    """
    Read and set the required parameters to dict

    :param params: the parameters to be used
    :return: a dict that includes required parameters
    """
    conf_dict = {}
    conf_dict['keepalive_interval'] = params.get("keepalive_interval", '5')
    conf_dict['log_level'] = params.get("log_level", '3')
    conf_dict['log_outputs'] = '"%s:file:%s"' % (params.get("log_level", '3'),
                                                 params.get("libvirt_log",
                                                 "/var/log/libvirt/libvirtd.log"))
    logging.debug("Assemble libvirtd configuration dict as below:%s\n",
                  conf_dict)
    return conf_dict


def run(test, params, env):
    def check_vm_network_accessed():
        """
        The operations to the VM need to be done before migration happens
        """
        # 1. Confirm local VM can be accessed through network.
        logging.info("Check local VM network connectivity before migrating")
        s_ping, o_ping = utils_test.ping(vm.get_address(), count=10, timeout=20)
        logging.info(o_ping)
        if s_ping != 0:
            test.error("%s did not respond after %d sec." % (vm.name, 20))

    def check_virsh_command_and_option(command, option=None):
        """
        Check if virsh command exists

        :param command: the command to be checked
        :param option: the command option to be checked
        """
        msg = "This version of libvirt does not support "
        if not virsh.has_help_command(command):
            test.cancel(msg + "virsh command '%s'" % command)

        if option and not virsh.has_command_help_match(command, option):
            test.cancel(msg + "virsh command '%s' with option '%s'" % (command,
                                                                       option))

    def do_migration(vm, dest_uri, options, extra):
        """
        Execute the migration with given parameters
        :param vm: the guest to be migrated
        :param dest_uri: the destination uri for migration
        :param options: options next to 'migrate' command
        :param extra: options in the end of the migrate command line

        :return: CmdResult object
        """
        logging.info("Sleeping 10 seconds before migration")
        time.sleep(10)
        # Migrate the guest.
        migration_res = vm.migrate(dest_uri, options, extra, True, True)
        logging.info("Migration out: %s", results_stdout_52lts(migration_res).strip())
        logging.info("Migration error: %s", results_stderr_52lts(migration_res).strip())
        if int(migration_res.exit_status) != 0:
            logging.error("Migration failed for %s.", vm_name)
            return migration_res

        if vm.is_alive():  # vm.connect_uri was updated
            logging.info("VM is alive on destination %s.", dest_uri)
        else:
            test.fail("VM is not alive on destination %s" % dest_uri)

        # Throws exception if console shows panic message
        vm.verify_kernel_crash()
        return migration_res

    def cleanup_libvirtd_log(log_file):
        """
        Remove existing libvirtd log file on source and target host.

        :param log_file: log file with absolute path
        """
        if os.path.exists(log_file):
            logging.debug("Delete local libvirt log file '%s'", log_file)
            os.remove(log_file)
        cmd = "rm -f %s" % log_file
        logging.debug("Delete remote libvirt log file '%s'", log_file)
        run_remote_cmd(cmd)

    def cleanup_dest(vm):
        """
        Clean up the destination host environment
        when doing the uni-direction migration.

        :param vm: the guest to be cleaned up
        """
        logging.info("Cleaning up VMs on %s", vm.connect_uri)
        try:
            if virsh.domain_exists(vm.name, uri=vm.connect_uri):
                vm_state = vm.state()
                if vm_state == "paused":
                    vm.resume()
                elif vm_state == "shut off":
                    vm.start()
                vm.destroy(gracefully=False)

                if vm.is_persistent():
                    vm.undefine()

        except Exception as detail:
            logging.error("Cleaning up destination failed.\n%s", detail)

    def run_remote_cmd(cmd):
        """
        A function to run a command on remote host.

        :param cmd: the command to be executed

        :return: CmdResult object
        """
        remote_runner = remote.RemoteRunner(host=server_ip,
                                            username=server_user,
                                            password=server_pwd)
        cmdResult = remote_runner.run(cmd, ignore_status=True)
        if cmdResult.exit_status:
            test.fail("Failed to run '%s' on remote: %s"
                      % (cmd, results_stderr_52lts(cmdResult).strip()))
        return cmdResult

    def run_stress_in_vm():
        """
        The function to load stress in VM
        """
        stress_args = params.get("stress_args", "--cpu 8 --io 4 "
                                 "--vm 2 --vm-bytes 128M "
                                 "--timeout 20s")
        try:
            vm_session.cmd('stress %s' % stress_args)
        except Exception as detail:
            logging.debug(detail)

    def check_timeout_postcopy(params):
        """
        Check the vm state on target host after timeout
        when --postcopy and --timeout-postcopy are used.
        The vm state is expected as running.

        :param params: the parameters used
        """
        timeout = int(params.get("timeout_postcopy", 10))
        time.sleep(timeout + 1)
        remote_virsh_session = virsh.VirshPersistent(**remote_virsh_dargs)
        vm_state = results_stdout_52lts(remote_virsh_session.domstate(vm_name)).strip()
        if vm_state != "running":
            remote_virsh_session.close_session()
            test.fail("After timeout '%s' seconds, "
                      "the vm state on target host should "
                      "be 'running', but '%s' found",
                      timeout, vm_state)
        remote_virsh_session.close_session()

    def get_usable_compress_cache(pagesize):
        """
        Get a number which is bigger than pagesize and is power of two.

        :param pagesize: the given integer
        :return: an integer satisfying the criteria
        """
        def calculate(num):
            result = num & (num - 1)
            return (result == 0)

        item = pagesize
        found = False
        while (not found):
            item += 1
            found = calculate(item)
        logging.debug("%d is smallest one that is bigger than '%s' and "
                      "is power of 2", item, pagesize)
        return item

    check_parameters(test, params)

    # Params for NFS shared storage
    shared_storage = params.get("migrate_shared_storage", "")
    if shared_storage == "":
        default_guest_asset = defaults.get_default_guest_os_info()['asset']
        default_guest_asset = "%s.qcow2" % default_guest_asset
        shared_storage = os.path.join(params.get("nfs_mount_dir"),
                                      default_guest_asset)
        logging.debug("shared_storage:%s", shared_storage)

    # params for migration connection
    params["virsh_migrate_desturi"] = libvirt_vm.complete_uri(
                                       params.get("migrate_dest_host"))
    # Params to update disk using shared storage
    params["disk_type"] = "file"
    params["disk_source_protocol"] = "netfs"
    params["mnt_path_name"] = params.get("nfs_mount_dir")

    # Local variables
    server_ip = params.get("server_ip")
    server_user = params.get("server_user", "root")
    server_pwd = params.get("server_pwd")
    extra = params.get("virsh_migrate_extra")
    options = params.get("virsh_migrate_options")
    src_uri = params.get("virsh_migrate_connect_uri")
    dest_uri = params.get("virsh_migrate_desturi")
    log_file = params.get("libvirt_log", "/var/log/libvirt/libvirtd.log")
    check_complete_job = "yes" == params.get("check_complete_job", "no")
    config_libvirtd = "yes" == params.get("config_libvirtd", "no")
    grep_str_remote_log = params.get("grep_str_remote_log", "")
    grep_str_local_log = params.get("grep_str_local_log", "")
    stress_in_vm = "yes" == params.get("stress_in_vm", "no")
    remote_virsh_dargs = {'remote_ip': server_ip, 'remote_user': server_user,
                          'remote_pwd': server_pwd, 'unprivileged_user': None,
                          'ssh_remote_auth': True}

    # For TLS
    tls_recovery = params.get("tls_auto_recovery", "yes")
    # qemu config
    qemu_conf_dict = None
    # libvirtd config
    libvirtd_conf_dict = None

    remote_virsh_session = None
    vm = None
    vm_session = None
    libvirtd_conf = None
    qemu_conf = None
    mig_result = None
    test_exception = None
    is_TestError = False
    is_TestFail = False
    is_TestSkip = False
    asynch_migration = False

    # Objects to be cleaned up in the end
    objs_list = []
    tls_obj = None

    # Local variables
    vm_name = params.get("migrate_main_vm")
    vm = env.get_vm(vm_name)
    vm.verify_alive()

    # For safety reasons, we'd better back up  xmlfile.
    orig_config_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    if not orig_config_xml:
        test.error("Backing up xmlfile failed.")

    try:
        # Change the disk of the vm to shared disk
        libvirt.set_vm_disk(vm, params)

        if extra.count("--tls"):
            qemu_conf_dict = {"migrate_tls_x509_verify": "1"}
            # Setup TLS
            tls_obj = TLSConnection(params)
            if tls_recovery == "yes":
                objs_list.append(tls_obj)
                tls_obj.auto_recover = True
                tls_obj.conn_setup()
            # Setup qemu configure
            logging.debug("Configure the qemu")
            cleanup_libvirtd_log(log_file)
            qemu_conf = libvirt.customize_libvirt_config(qemu_conf_dict,
                                                         config_type="qemu",
                                                         remote_host=True,
                                                         extra_params=params)
        # Setup libvirtd
        if config_libvirtd:
            logging.debug("Configure the libvirtd")
            cleanup_libvirtd_log(log_file)
            libvirtd_conf_dict = setup_libvirtd_conf_dict(params)
            libvirtd_conf = libvirt.customize_libvirt_config(libvirtd_conf_dict,
                                                             remote_host=True,
                                                             extra_params=params)
        if not vm.is_alive():
            vm.start()
        vm_session = vm.wait_for_login()
        check_vm_network_accessed()

        if stress_in_vm:
            pkg_name = 'stress'
            logging.debug("Check if stress tool is installed")
            pkg_mgr = utils_package.package_manager(vm_session, pkg_name)
            if not pkg_mgr.is_installed(pkg_name):
                logging.debug("Stress tool will be installed")
                if not pkg_mgr.install():
                    test.error("Package '%s' installation fails" % pkg_name)

            stress_thread = threading.Thread(target=run_stress_in_vm,
                                             args=())
            stress_thread.start()

        if extra.count("timeout-postcopy"):
            asynch_migration = True
            func_name = check_timeout_postcopy
        if extra.count("comp-xbzrle-cache"):
            cache = get_usable_compress_cache(memory.get_page_size())
            extra = "%s %s" % (extra, cache)

        # For --postcopy enable
        postcopy_options = params.get("postcopy_options")
        if postcopy_options and not extra.count(postcopy_options):
            extra = "%s %s" % (extra, postcopy_options)

        if not asynch_migration:
            mig_result = do_migration(vm, dest_uri, options, extra)
        else:
            migration_test = libvirt.MigrationTest()

            logging.debug("vm.connect_uri=%s", vm.connect_uri)
            vms = [vm]
            try:
                migration_test.do_migration(vms, None, dest_uri, 'orderly',
                                            options, thread_timeout=900,
                                            ignore_status=True,
                                            func=func_name, extra_opts=extra,
                                            func_params=params)
                mig_result = migration_test.ret
            except exceptions.TestFail as fail_detail:
                test.fail(fail_detail)
            except exceptions.TestSkipError as skip_detail:
                test.cancel(skip_detail)
            except exceptions.TestError as error_detail:
                test.error(error_detail)
            except Exception as details:
                mig_result = migration_test.ret
                logging.error(details)

        if int(mig_result.exit_status) != 0:
            test.fail(results_stderr_52lts(mig_result).strip())
        if check_complete_job:
            search_str_domjobinfo = params.get("search_str_domjobinfo", None)
            opts = "--completed"
            args = vm_name + " " + opts
            check_virsh_command_and_option("domjobinfo", opts)
            jobinfo = results_stdout_52lts(virsh.domjobinfo(args, debug=True,
                                           ignore_status=True)).strip()
            logging.debug("Local job info on completion:\n%s", jobinfo)
            if extra.count("comp-xbzrle-cache") and search_str_domjobinfo:
                search_str_domjobinfo = "%s %s" % (search_str_domjobinfo, cache//1024)
            if search_str_domjobinfo:
                if not re.search(search_str_domjobinfo, jobinfo):
                    test.fail("Fail to search '%s' on local:\n%s"
                              % (search_str_domjobinfo, jobinfo))
            # Check remote host
            remote_virsh_session = virsh.VirshPersistent(**remote_virsh_dargs)
            jobinfo = results_stdout_52lts(remote_virsh_session.domjobinfo(args, debug=True,
                                                                           ignore_status=True)).strip()
            logging.debug("Remote job info on completion:\n%s", jobinfo)
            if search_str_domjobinfo:
                if not re.search(search_str_domjobinfo, jobinfo):
                    remote_virsh_session.close_session()
                    test.fail("Fail to search '%s' on remote:\n%s"
                              % (search_str_domjobinfo, jobinfo))
            remote_virsh_session.close_session()
        if grep_str_local_log:
            cmd = "grep -E '%s' %s" % (grep_str_local_log, log_file)
            cmdRes = process.run(cmd, shell=True, ignore_status=True)
            if cmdRes.exit_status:
                test.fail(results_stderr_52lts(cmdRes).strip())
        if grep_str_remote_log:
            cmd = "grep -E '%s' %s" % (grep_str_remote_log, log_file)
            run_remote_cmd(cmd)

    except exceptions.TestFail as details:
        is_TestFail = True
        test_exception = details
    except exceptions.TestSkipError as details:
        is_TestSkip = True
        test_exception = details
    except exceptions.TestError as details:
        is_TestError = True
        test_exception = details
    except Exception as details:
        test_exception = details
    finally:
        logging.debug("Recover test environment")
        try:
            # Clean VM on destination
            vm.connect_uri = dest_uri
            cleanup_dest(vm)
            vm.connect_uri = src_uri

            logging.info("Recovery VM XML configration")
            orig_config_xml.sync()
            logging.debug("The current VM XML:\n%s", orig_config_xml.xmltreefile)

            if remote_virsh_session:
                remote_virsh_session.close_session()

            if extra.count("--tls"):
                logging.debug("Recover the qemu configuration")
                libvirt.customize_libvirt_config(None,
                                                 config_type="qemu",
                                                 remote_host=True,
                                                 extra_params=params,
                                                 is_recover=True,
                                                 config_object=qemu_conf)

            if config_libvirtd:
                logging.debug("Recover the libvirtd configuration")
                libvirt.customize_libvirt_config(None,
                                                 remote_host=True,
                                                 extra_params=params,
                                                 is_recover=True,
                                                 config_object=libvirtd_conf)

            logging.info("Remove local NFS image")
            source_file = params.get("source_file")
            libvirt.delete_local_disk("file", path=source_file)

            if objs_list:
                for obj in objs_list:
                    logging.debug("Clean up local objs")
                    del obj

        except Exception as exception_detail:
            if (not test_exception and not is_TestError and
               not is_TestFail and not is_TestSkip):
                raise exception_detail
            else:
                # if any of above exceptions has been raised, only print
                # error log here to avoid of hiding the original issue
                logging.error(exception_detail)
    # Check result
    if is_TestFail:
        test.fail(test_exception)
    if is_TestSkip:
        test.cancel(test_exception)
    if is_TestError:
        test.error(test_exception)
    if not test_exception:
        logging.info("Case execution is done.")
    else:
        test.error(test_exception)
