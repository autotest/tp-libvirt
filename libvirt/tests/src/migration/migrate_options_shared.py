import os
import logging
import time
import math
import re
import threading
import platform
import tempfile

from avocado.utils import process
from avocado.utils import memory
from avocado.core import exceptions

from virttest import libvirt_vm
from virttest import utils_test
from virttest import defaults
from virttest import data_dir
from virttest import virsh
from virttest import remote
from virttest import utils_package
from virttest import xml_utils

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.utils_conn import TLSConnection
from virttest.compat_52lts import results_stdout_52lts, results_stderr_52lts
from virttest.libvirt_xml.devices.controller import Controller


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
    """
    Test virsh migrate command.
    """

    def set_feature(vmxml, feature, value):
        """
        Set guest features for PPC

        :param state: the htm status
        :param vmxml: guest xml
        """
        features_xml = vm_xml.VMFeaturesXML()
        if feature == 'hpt':
            features_xml.hpt_resizing = value
        elif feature == 'htm':
            features_xml.htm = value
        vmxml.features = features_xml
        vmxml.sync()

    def trigger_hpt_resize(session):
        """
        Check the HPT order file and dmesg

        :param session: the session to guest

        :raise: test.fail if required message is not found
        """
        hpt_order_path = "/sys/kernel/debug/powerpc/hpt_order"
        hpt_order = session.cmd_output('cat %s' % hpt_order_path).strip()
        hpt_order = int(hpt_order)
        logging.info('Current hpt_order is %d', hpt_order)
        hpt_order += 1
        cmd = 'echo %d > %s' % (hpt_order, hpt_order_path)
        cmd_result = session.cmd_status_output(cmd)
        result = process.CmdResult(stderr=cmd_result[1],
                                   stdout=cmd_result[1],
                                   exit_status=cmd_result[0])
        libvirt.check_exit_status(result)
        dmesg = session.cmd('dmesg')
        dmesg_content = params.get('dmesg_content').split('|')
        for content in dmesg_content:
            if content % hpt_order not in dmesg:
                test.fail("'%s' is missing in dmesg" % (content % hpt_order))
            else:
                logging.info("'%s' is found in dmesg", content % hpt_order)

    def check_vm_network_accessed(session=None):
        """
        The operations to the VM need to be done before or after
        migration happens

        :param session: The session object to the host

        :raise: test.error when ping fails
        """
        # Confirm local/remote VM can be accessed through network.
        logging.info("Check VM network connectivity")
        s_ping, _ = utils_test.ping(vm.get_address(),
                                    count=10,
                                    timeout=20,
                                    output_func=logging.debug,
                                    session=session)
        if s_ping != 0:
            if session:
                session.close()
            test.fail("%s did not respond after %d sec." % (vm.name, 20))

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

    def add_ctrls(vm_xml, dev_type="pci", dev_index="0", dev_model="pci-root"):
        """
        Add multiple devices

        :param dev_type: the type of the device to be added
        :param dev_index: the maximum index of the device to be added
        :param dev_model: the model of the device to be added
        """
        for inx in range(0, int(dev_index) + 1):
            newcontroller = Controller("controller")
            newcontroller.type = dev_type
            newcontroller.index = inx
            newcontroller.model = dev_model
            logging.debug("New device is added:\n%s", newcontroller)
            vm_xml.add_device(newcontroller)
        vm_xml.sync()

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
        virsh_args.update({"ignore_status": True})
        migration_res = vm.migrate(dest_uri, options, extra, **virsh_args)
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
        cmd_parms = {'server_ip': server_ip, 'server_user': server_user, 'server_pwd': server_pwd}
        remote.run_remote_cmd(cmd, cmd_parms, runner_on_target)

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

    def control_migrate_speed(to_speed=1):
        """
        Control migration duration

        :param to_speed: the speed value in Mbps to be set for migration
        :return int: the new migration speed after setting
        """
        virsh_args.update({"ignore_status": False})
        old_speed = virsh.migrate_getspeed(vm_name, **virsh_args)
        logging.debug("Current migration speed is %s MiB/s\n", old_speed.stdout.strip())
        logging.debug("Set migration speed to %d MiB/s\n", to_speed)
        cmd_result = virsh.migrate_setspeed(vm_name, to_speed, "", **virsh_args)
        actual_speed = virsh.migrate_getspeed(vm_name, **virsh_args)
        logging.debug("New migration speed is %s MiB/s\n", actual_speed.stdout.strip())
        return int(actual_speed.stdout.strip())

    def check_setspeed(params):
        """
        Set/get migration speed

        :param params: the parameters used
        :raise: test.fail if speed set does not take effect
        """
        expected_value = int(params.get("migrate_speed", '41943040')) // (1024 * 1024)
        actual_value = control_migrate_speed(to_speed=expected_value)
        params.update({'compare_to_value': actual_value})
        if actual_value != expected_value:
            test.fail("Migration speed is expected to be '%d MiB/s', but '%d MiB/s' "
                      "found" % (expected_value, actual_value))

    def check_domjobinfo(params, option=""):
        """
        Check given item in domjobinfo of the guest is as expected

        :param params: the parameters used
        :param option: options for domjobinfo
        :raise: test.fail if the value of given item is unexpected
        """
        def search_jobinfo(jobinfo):
            """
            Find value of given item in domjobinfo

            :param jobinfo: cmdResult object
            :raise: test.fail if not found
            """
            for item in jobinfo.stdout.splitlines():
                if item.count(jobinfo_item):
                    groups = re.findall(r'[0-9.]+', item.strip())
                    logging.debug("In '%s' search '%s'\n", item, groups[0])
                    if (math.fabs(float(groups[0]) - float(compare_to_value)) //
                       float(compare_to_value) > diff_rate):
                        test.fail("{} {} has too much difference from "
                                  "{}".format(jobinfo_item,
                                              groups[0],
                                              compare_to_value))
                break

        jobinfo_item = params.get("jobinfo_item")
        compare_to_value = params.get("compare_to_value")
        logging.debug("compare_to_value:%s", compare_to_value)
        diff_rate = float(params.get("diff_rate", "0"))
        if not jobinfo_item or not compare_to_value:
            return
        vm_ref = '{}{}'.format(vm_name, option)
        jobinfo = virsh.domjobinfo(vm_ref, **virsh_args)
        search_jobinfo(jobinfo)

        check_domjobinfo_remote = params.get("check_domjobinfo_remote")
        if check_domjobinfo_remote:
            remote_virsh_session = virsh.VirshPersistent(**remote_virsh_dargs)
            jobinfo = remote_virsh_session.domjobinfo(vm_ref, **virsh_args)
            search_jobinfo(jobinfo)
            remote_virsh_session.close_session()

    def check_maxdowntime(params):
        """
        Set/get migration maxdowntime

        :param params: the parameters used
        :raise: test.fail if maxdowntime set does not take effect
        """
        expected_value = int(float(params.get("migrate_maxdowntime", '0.3')) * 1000)
        virsh_args.update({"ignore_status": False})
        old_value = int(virsh.migrate_getmaxdowntime(vm_name).stdout.strip())
        logging.debug("Current migration maxdowntime is %d ms", old_value)
        logging.debug("Set migration maxdowntime to %d ms", expected_value)
        virsh.migrate_setmaxdowntime(vm_name, expected_value, **virsh_args)
        actual_value = int(virsh.migrate_getmaxdowntime(vm_name).stdout.strip())
        logging.debug("New migration maxdowntime is %d ms", actual_value)
        if actual_value != expected_value:
            test.fail("Migration maxdowntime is expected to be '%d ms', but '%d ms' "
                      "found" % (expected_value, actual_value))
        params.update({'compare_to_value': actual_value})

    def do_actions_during_migrate(params):
        """
        The entry point to execute action list during migration

        :param params: the parameters used
        """
        actions_during_migration = params.get("actions_during_migration")
        if not actions_during_migration:
            return
        for action in actions_during_migration.split(","):
            if action == 'setspeed':
                check_setspeed(params)
            elif action == 'domjobinfo':
                check_domjobinfo(params)
            elif action == 'setmaxdowntime':
                check_maxdowntime(params)
            time.sleep(3)

    def attach_channel_xml():
        """
        Create channel xml and attach it to guest configuration
        """
        # Check if pty channel exists already
        for elem in new_xml.devices.by_device_tag('channel'):
            if elem.type_name == channel_type_name:
                logging.debug("{0} channel already exists in guest. "
                              "No need to add new one".format(channel_type_name))
                return

        params = {'channel_type_name': channel_type_name,
                  'target_type': target_type,
                  'target_name': target_name}
        channel_xml = libvirt.create_channel_xml(params)
        virsh.attach_device(domain_opt=vm_name, file_opt=channel_xml.xml,
                            flagstr="--config", ignore_status=False)
        logging.debug("New VMXML with channel:\n%s", virsh.dumpxml(vm_name))

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

    def check_migration_res(result):
        """
        Check if the migration result is as expected

        :param result: the output of migration
        :raise: test.fail if test is failed
        """
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
    virsh_args = {"debug": True}
    virsh_opt = params.get("virsh_opt", "")
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
    contrl_index = params.get("new_contrl_index", None)
    asynch_migration = "yes" == params.get("asynch_migrate", "no")
    grep_str_remote_log = params.get("grep_str_remote_log", "")
    grep_str_local_log = params.get("grep_str_local_log", "")
    disable_verify_peer = "yes" == params.get("disable_verify_peer", "no")
    status_error = "yes" == params.get("status_error", "no")
    stress_in_vm = "yes" == params.get("stress_in_vm", "no")
    low_speed = params.get("low_speed", None)
    remote_virsh_dargs = {'remote_ip': server_ip, 'remote_user': server_user,
                          'remote_pwd': server_pwd, 'unprivileged_user': None,
                          'ssh_remote_auth': True}

    hpt_resize = params.get("hpt_resize", None)
    htm_state = params.get("htm_state", None)

    # For pty channel test
    add_channel = "yes" == params.get("add_channel", "no")
    channel_type_name = params.get("channel_type_name", None)
    target_type = params.get("target_type", None)
    target_name = params.get("target_name", None)
    cmd_run_in_remote_guest = params.get("cmd_run_in_remote_guest", None)
    cmd_run_in_remote_guest_1 = params.get("cmd_run_in_remote_guest_1", None)
    cmd_run_in_remote_host = params.get("cmd_run_in_remote_host", None)
    cmd_run_in_remote_host_1 = params.get("cmd_run_in_remote_host_1", None)
    cmd_run_in_remote_host_2 = params.get("cmd_run_in_remote_host_2", None)

    # For qemu command line checking
    qemu_check = params.get("qemu_check", None)

    xml_check_after_mig = params.get("guest_xml_check_after_mig", None)

    # params for cache matrix test
    cache = params.get("cache")
    remove_cache = "yes" == params.get("remove_cache", "no")
    err_msg = params.get("err_msg")

    arch = platform.machine()
    if any([hpt_resize, contrl_index, htm_state]) and 'ppc64' not in arch:
        test.cancel("The case is PPC only.")

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

    # Objects to be cleaned up in the end
    objs_list = []
    tls_obj = None

    # Local variables
    vm_name = params.get("migrate_main_vm")
    vm = env.get_vm(vm_name)
    vm.verify_alive()

    # For safety reasons, we'd better back up  xmlfile.
    new_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    orig_config_xml = new_xml.copy()
    if not orig_config_xml:
        test.error("Backing up xmlfile failed.")

    try:
        # Create a remote runner for later use
        runner_on_target = remote.RemoteRunner(host=server_ip,
                                               username=server_user,
                                               password=server_pwd)

        # Change the configuration files if needed before starting guest
        # For qemu.conf
        if extra.count("--tls"):
            # Setup TLS
            tls_obj = TLSConnection(params)
            if tls_recovery == "yes":
                objs_list.append(tls_obj)
                tls_obj.auto_recover = True
                tls_obj.conn_setup()
            if not disable_verify_peer:
                qemu_conf_dict = {"migrate_tls_x509_verify": "1"}
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
        # Prepare required guest xml before starting guest
        if contrl_index:
            new_xml.remove_all_device_by_type('controller')
            logging.debug("After removing controllers, current XML:\n%s\n", new_xml)
            add_ctrls(new_xml, dev_index=contrl_index)

        if add_channel:
            attach_channel_xml()

        if hpt_resize:
            set_feature(new_xml, 'hpt', hpt_resize)

        if htm_state:
            set_feature(new_xml, 'htm', htm_state)

        if cache:
            params["driver_cache"] = cache
        if remove_cache:
            params["enable_cache"] = "no"

        # Change the disk of the vm to shared disk and then start VM
        libvirt.set_vm_disk(vm, params)

        if not vm.is_alive():
            vm.start()

        logging.debug("Guest xml after starting:\n%s", vm_xml.VMXML.new_from_dumpxml(vm_name))

        # Check qemu command line after guest is started
        if qemu_check:
            check_content = qemu_check
            if hpt_resize:
                check_content = "%s%s" % (qemu_check, hpt_resize)
            if htm_state:
                check_content = "%s%s" % (qemu_check, htm_state)
            libvirt.check_qemu_cmd_line(check_content)

        # Check local guest network connection before migration
        vm_session = vm.wait_for_login()
        check_vm_network_accessed()

        # Preparation for the running guest before migration
        if hpt_resize and hpt_resize != 'disabled':
            trigger_hpt_resize(vm_session)

        if low_speed:
            control_migrate_speed(int(low_speed))

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
            func_name = check_timeout_postcopy
        if params.get("actions_during_migration"):
            func_name = do_actions_during_migrate
        if extra.count("comp-xbzrle-cache"):
            cache = get_usable_compress_cache(memory.get_page_size())
            extra = "%s %s" % (extra, cache)

        # For --postcopy enable
        postcopy_options = params.get("postcopy_options")
        if postcopy_options:
            extra = "%s %s" % (extra, postcopy_options)

        # Execute migration process
        if not asynch_migration:
            mig_result = do_migration(vm, dest_uri, options, extra)
        else:
            migration_test = libvirt.MigrationTest()

            logging.debug("vm.connect_uri=%s", vm.connect_uri)
            vms = [vm]
            try:
                migration_test.do_migration(vms, None, dest_uri, 'orderly',
                                            options, thread_timeout=900,
                                            ignore_status=True, virsh_opt=virsh_opt,
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

        check_migration_res(mig_result)

        if add_channel:
            # Get the channel device source path of remote guest
            if not remote_virsh_session:
                remote_virsh_session = virsh.VirshPersistent(**remote_virsh_dargs)
            file_path = tempfile.mktemp(dir=data_dir.get_tmp_dir())
            remote_virsh_session.dumpxml(vm_name, to_file=file_path,
                                         debug=True,
                                         ignore_status=True)
            local_vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
            local_vmxml.xmltreefile = xml_utils.XMLTreeFile(file_path)
            for elem in local_vmxml.devices.by_device_tag('channel'):
                logging.debug("Found channel device {}".format(elem))
                if elem.type_name == channel_type_name:
                    host_source = elem.source.get('path')
                    logging.debug("Remote guest uses {} for channel device".format(host_source))
                    break
            remote_virsh_session.close_session()
            if not host_source:
                test.fail("Can not find source for %s channel on remote host" % channel_type_name)

            # Prepare to wait for message on remote host from the channel
            cmd_parms = {'server_ip': server_ip, 'server_user': server_user, 'server_pwd': server_pwd}
            cmd_result = remote.run_remote_cmd(cmd_run_in_remote_host % host_source,
                                               cmd_parms,
                                               runner_on_target)

            # Send message from remote guest to the channel file
            remote_vm_obj = utils_test.RemoteVMManager(cmd_parms)
            vm_ip = vm.get_address()
            vm_pwd = params.get("password")
            remote_vm_obj.setup_ssh_auth(vm_ip, vm_pwd)
            cmd_result = remote_vm_obj.run_command(vm_ip, cmd_run_in_remote_guest_1)
            remote_vm_obj.run_command(vm_ip, cmd_run_in_remote_guest % results_stdout_52lts(cmd_result).strip())
            logging.debug("Sending message is done")

            # Check message on remote host from the channel
            remote.run_remote_cmd(cmd_run_in_remote_host_1, cmd_parms, runner_on_target)
            logging.debug("Receiving message is done")
            remote.run_remote_cmd(cmd_run_in_remote_host_2, cmd_parms, runner_on_target)

        if check_complete_job:
            opts = " --completed"
            check_virsh_command_and_option("domjobinfo", opts)
            if extra.count("comp-xbzrle-cache"):
                params.update({'compare_to_value': cache // 1024})
            check_domjobinfo(params, option=opts)

        if grep_str_local_log:
            cmd = "grep -E '%s' %s" % (grep_str_local_log, log_file)
            cmdRes = process.run(cmd, shell=True, ignore_status=True)
            if cmdRes.exit_status:
                test.fail(results_stderr_52lts(cmdRes).strip())
        if grep_str_remote_log:
            cmd = "grep -E '%s' %s" % (grep_str_remote_log, log_file)
            cmd_parms = {'server_ip': server_ip, 'server_user': server_user, 'server_pwd': server_pwd}
            remote.run_remote_cmd(cmd, cmd_parms, runner_on_target)

        if xml_check_after_mig:
            if not remote_virsh_session:
                remote_virsh_session = virsh.VirshPersistent(**remote_virsh_dargs)
            target_guest_dumpxml = results_stdout_52lts(
                remote_virsh_session.dumpxml(vm_name,
                                             debug=True,
                                             ignore_status=True)).strip()
            if hpt_resize:
                check_str = hpt_resize
            elif htm_state:
                check_str = htm_state
            if hpt_resize or htm_state:
                xml_check_after_mig = "%s'%s'" % (xml_check_after_mig, check_str)
                if not re.search(xml_check_after_mig, target_guest_dumpxml):
                    remote_virsh_session.close_session()
                    test.fail("Fail to search '%s' in target guest XML:\n%s"
                              % (xml_check_after_mig, target_guest_dumpxml))

            if contrl_index:
                all_ctrls = re.findall(xml_check_after_mig, target_guest_dumpxml)
                if len(all_ctrls) != int(contrl_index) + 1:
                    remote_virsh_session.close_session()
                    test.fail("%s pci-root controllers are expected in guest XML, "
                              "but found %s" % (int(contrl_index) + 1, len(all_ctrls)))
            remote_virsh_session.close_session()

        if int(mig_result.exit_status) == 0:
            server_session = remote.wait_for_login('ssh', server_ip, '22',
                                                   server_user, server_pwd,
                                                   r"[\#\$]\s*$")
            check_vm_network_accessed(server_session)
            server_session.close()
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

            if extra.count("--tls") and not disable_verify_peer:
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
