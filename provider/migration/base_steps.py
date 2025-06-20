import os
import platform
import time

from six import itervalues

from virttest import data_dir
from virttest import migration
from virttest import libvirt_remote
from virttest import libvirt_vm
from virttest import remote
from virttest import utils_config
from virttest import utils_conn
from virttest import utils_libvirtd
from virttest import utils_iptables
from virttest import utils_misc
from virttest import virsh

from virttest.utils_libvirt import libvirt_disk
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml

from provider.migration import migration_base


class MigrationBase(object):
    """
    Class for migration base steps

    :param test: test object
    :param vm: vm object
    :param params: Dictionary with the test parameter
    :param migration_test: MigrationTest object
    :param src_uri: source uri
    :param conn_list: connection object list
    :param remote_libvirtd_log: remote.RemoteFile object
    """

    def __init__(self, test, vm, params):
        """
        Init params and other necessary variables

        """
        self.test = test
        self.vm = vm
        self.params = params
        self.src_uri = vm.connect_uri
        self.src_full_uri = libvirt_vm.complete_uri(
                        self.params.get("migrate_source_host"))
        self.conn_list = []
        self.check_cont_ping = "yes" == self.params.get("check_cont_ping", "no")
        self.check_cont_ping_log = self.params.get("check_cont_ping_log", "/tmp/log_file")
        self.remote_libvirtd_log = None

        migration_test = migration.MigrationTest()
        migration_test.check_parameters(params)
        self.migration_test = migration_test

        # Back up xmlfile.
        vm_name = params.get("migrate_main_vm")
        new_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        self.orig_config_xml = new_xml.copy()

    def setup_default(self):
        """
        Setup steps by default

        """
        set_remote_libvirtd_log = "yes" == self.params.get("set_remote_libvirtd_log", "no")
        start_vm = self.params.get("start_vm", "yes")
        nfs_mount_dir = self.params.get("nfs_mount_dir")

        self.test.log.info("Setup steps by default.")
        if set_remote_libvirtd_log:
            self.set_remote_log()

        if nfs_mount_dir:
            disk_dict = {'source': {'attrs': {'file': os.path.join(nfs_mount_dir,
                         os.path.basename(self.vm.get_first_disk_devices()['source']))}}}
        else:
            default_image_path = os.path.join(data_dir.get_data_dir(), 'images')
            disk_dict = {'source': {'attrs': {'file': os.path.join(default_image_path,
                         os.path.basename(self.vm.get_first_disk_devices()['source']))}}}

        libvirt_vmxml.modify_vm_device(
                vm_xml.VMXML.new_from_inactive_dumpxml(self.vm.name),
                'disk', disk_dict)

        if start_vm == "no" and self.vm.is_alive():
            self.vm.destroy()

        if start_vm == "yes" and not self.vm.is_alive():
            self.vm.start()
            self.vm.wait_for_login().close()
        if self.check_cont_ping:
            self.test.log.debug("Starting ping command to check network during migration...")
            vm_session = self.vm.wait_for_login()
            ping_cmd = "ping 8.8.8.8 > %s 2>&1 &" % self.check_cont_ping_log
            vm_session.sendline(ping_cmd)

    def run_migration(self):
        """
        Execute migration from source host to target host

        """
        virsh_options = self.params.get("virsh_options", "")
        options = self.params.get("virsh_migrate_options", "--live --verbose")
        dest_uri = self.params.get("virsh_migrate_desturi")
        vm_name = self.params.get("migrate_main_vm")
        action_during_mig = self.params.get("action_during_mig")
        migrate_speed = self.params.get("migrate_speed")
        stress_package = self.params.get("stress_package")
        check_vm_conn_before_migration = "yes" == self.params.get("check_vm_conn_before_migration", "no")
        extra = self.params.get("virsh_migrate_extra")
        extra_args = self.migration_test.update_virsh_migrate_extra_args(self.params)
        postcopy_options = self.params.get("postcopy_options")
        postcopy_options_during_mig = self.params.get("postcopy_options_during_mig")
        do_migration_during_mig = "yes" == self.params.get("do_migration_during_mig", "no")
        initiating_bandwidth = self.params.get("initiating_bandwidth")
        second_bandwidth = self.params.get("second_bandwidth")
        copy_storage_option = self.params.get("copy_storage_option")

        if postcopy_options:
            extra = "%s %s" % (extra, postcopy_options)
        if copy_storage_option:
            extra = "%s %s" % (extra, copy_storage_option)

        if check_vm_conn_before_migration:
            # Check local guest network connection before migration
            self.migration_test.ping_vm(self.vm, self.params)
        self.test.log.debug("Guest xml after starting:\n%s",
                            vm_xml.VMXML.new_from_dumpxml(vm_name))

        if action_during_mig:
            if do_migration_during_mig:
                action_list = eval(action_during_mig)
                for index in range(len(action_list)):
                    if action_list[index]['func'] == 'do_migration':
                        if postcopy_options_during_mig:
                            if second_bandwidth:
                                temp = self.params.get("postcopy_options").replace("--postcopy-bandwidth %s" % initiating_bandwidth, "--postcopy-bandwidth %s" % second_bandwidth)
                                extra_during_mig = "%s %s %s" % (self.params.get("virsh_migrate_extra"), temp, postcopy_options_during_mig)
                            else:
                                extra_during_mig = "%s %s" % (extra, postcopy_options_during_mig)
                        else:
                            extra_during_mig = extra
                        action_during_mig = migration_base.parse_funcs(action_during_mig,
                                                                       self.test, self.params)
                        status_error_during_mig = self.params.get("status_error_during_mig", "no")
                        extra_args_during_mig = self.migration_test.update_virsh_migrate_extra_args(self.params)
                        if status_error_during_mig:
                            extra_args_during_mig.update({'status_error': status_error_during_mig})
                            err_msg_during_mig = self.params.get("err_msg_during_mig")
                            if err_msg_during_mig:
                                extra_args_during_mig.update({'err_msg': err_msg_during_mig})
                        action_during_do_mig = self.params.get("action_during_do_mig")
                        if action_during_do_mig:
                            action_during_do_mig = migration_base.parse_funcs(action_during_do_mig, self.test, self.params)
                            action_params_during_mig = {"vm": self.vm, "mig_test": self.migration_test, "src_uri": None,
                                                        "dest_uri": dest_uri, "options": options, "virsh_options": virsh_options,
                                                        "extra": extra_during_mig, "action_during_mig": action_during_do_mig, "extra_args": extra_args_during_mig}
                        else:
                            action_params_during_mig = {"vm": self.vm, "mig_test": self.migration_test, "src_uri": None,
                                                        "dest_uri": dest_uri, "options": options, "virsh_options": virsh_options,
                                                        "extra": extra_during_mig, "action_during_mig": None, "extra_args": extra_args_during_mig}
                        action_during_mig[index].update({'func_param': action_params_during_mig})
                        break
            else:
                action_during_mig = migration_base.parse_funcs(action_during_mig,
                                                               self.test, self.params)

        mode = 'both' if postcopy_options else 'precopy'
        if migrate_speed:
            self.migration_test.control_migrate_speed(vm_name, int(migrate_speed), mode)
        if stress_package:
            self.migration_test.run_stress_in_vm(self.vm, self.params)

        # Execute migration process
        do_mig_param = {"vm": self.vm, "mig_test": self.migration_test, "src_uri": None,
                        "dest_uri": dest_uri, "options": options, "virsh_options": virsh_options,
                        "extra": extra, "action_during_mig": action_during_mig, "extra_args": extra_args}
        migration_base.do_migration(**do_mig_param)

    def run_migration_again(self):
        """
        Execute migration from source host to target host again

        """
        virsh_options = self.params.get("virsh_options", "")
        options = self.params.get("virsh_migrate_options", "--live --verbose")
        dest_uri = self.params.get("virsh_migrate_desturi")
        vm_name = self.params.get("migrate_main_vm")
        action_during_mig = self.params.get("action_during_mig")
        migrate_speed_again = self.params.get("migrate_speed_again")
        status_error = "yes" == self.params.get("status_error", "no")
        err_msg_again = self.params.get("err_msg_again")
        extra = self.params.get("virsh_migrate_extra")
        extra_args = self.migration_test.update_virsh_migrate_extra_args(self.params)
        postcopy_resume_migration = "yes" == self.params.get("postcopy_resume_migration", "no")
        postcopy_options = self.params.get("postcopy_options")
        copy_storage_option = self.params.get("copy_storage_option")

        if postcopy_options:
            extra = "%s %s" % (extra, postcopy_options)
        if copy_storage_option:
            extra = "%s %s" % (extra, copy_storage_option)

        if not postcopy_resume_migration:
            if not self.vm.is_alive():
                self.vm.connect_uri = self.src_uri
                self.vm.start()
            self.vm.wait_for_login().close()
        action_during_mig = migration_base.parse_funcs(self.params.get('action_during_mig_again'),
                                                       self.test, self.params)
        extra_args['status_error'] = self.params.get("migrate_again_status_error", "no")

        if err_msg_again:
            extra_args['err_msg'] = err_msg_again
        if self.params.get("virsh_migrate_extra_mig_again"):
            extra = self.params.get("virsh_migrate_extra_mig_again")
            if copy_storage_option:
                extra = "%s %s" % (extra, copy_storage_option)

        mode = 'both' if postcopy_options else 'precopy'
        if migrate_speed_again:
            self.migration_test.control_migrate_speed(vm_name,
                                                      int(migrate_speed_again),
                                                      mode)

        do_mig_param = {"vm": self.vm, "mig_test": self.migration_test, "src_uri": None,
                        "dest_uri": dest_uri, "options": options, "virsh_options": virsh_options,
                        "extra": extra, "action_during_mig": action_during_mig, "extra_args": extra_args}
        migration_base.do_migration(**do_mig_param)

    def run_migration_back(self):
        """
        Execute migration from target host to source host
        """
        migrate_vm_back = "yes" == self.params.get("migrate_vm_back", "yes")
        if not migrate_vm_back:
            return
        virsh_options = self.params.get("virsh_options", "")
        extra = self.params.get("virsh_migrate_extra")
        options = self.params.get("virsh_migrate_options", "--live --verbose")
        dest_uri = self.params.get("virsh_migrate_desturi")
        self.vm.connect_uri = dest_uri
        server_ip = self.params.get("server_ip")
        server_user = self.params.get("server_user", "root")
        server_pwd = self.params.get("server_pwd")

        client_ip = self.params.get("client_ip")
        client_pwd = self.params.get("client_pwd")
        runner_on_target = remote.RemoteRunner(host=server_ip,
                                               username=server_user,
                                               password=server_pwd)
        ssh_connection = utils_conn.SSHConnection(server_ip=client_ip,
                                                  server_pwd=client_pwd,
                                                  client_ip=server_ip,
                                                  client_pwd=server_pwd)
        try:
            ssh_connection.conn_check()
        except utils_conn.ConnectionError:
            ssh_connection.conn_setup()
            ssh_connection.conn_check()

        self.test.log.debug(self.params.get("migrate_source_host"))

        # Pre migration setup for remote machine
        self.migration_test.migrate_pre_setup(self.src_full_uri, self.params)

        cmd = "virsh migrate %s %s %s %s" % (self.vm.name, options,
                                             self.src_full_uri,
                                             extra)
        self.test.log.info("Start migration: %s", cmd)
        cmd_result = remote.run_remote_cmd(cmd, self.params, runner_on_target)
        if cmd_result.exit_status:
            self.test.fail("Failed to run '%s' on remote: %s"
                           % (cmd, cmd_result))
        self.vm.connect_uri = self.src_uri

    def check_vm_cont_ping(self, check_on_dest=True):
        """
        Check continuous ping command in VM

        :param check_on_dest: Check whether on the destination machine
        """
        dest_uri = self.params.get("virsh_migrate_desturi")
        if self.check_cont_ping:
            self.test.log.debug("Checking the output of %s", self.check_cont_ping_log)
            if check_on_dest:
                backup_uri, self.vm.connect_uri = self.vm.connect_uri, dest_uri
            self.vm.cleanup_serial_console()
            self.vm.create_serial_console()
            vm_session = self.vm.wait_for_serial_login(timeout=360)
            vm_session.cmd(
                "> {0}; sleep 5; grep time= {0}".format(self.check_cont_ping_log))
            o = vm_session.cmd_output(f"cat {self.check_cont_ping_log}")
            self.test.log.debug(f"ping command output: {o}")
            vm_session.close()
            if check_on_dest:
                self.vm.connect_uri = backup_uri

    def verify_default(self):
        """
        Verify steps by default

        """
        dest_uri = self.params.get("virsh_migrate_desturi")
        vm_name = self.params.get("migrate_main_vm")

        func_returns = dict(self.migration_test.func_ret)
        self.migration_test.func_ret.clear()
        self.test.log.debug("Migration returns function results:%s", func_returns)
        if int(self.migration_test.ret.exit_status) == 0:
            self.migration_test.post_migration_check([self.vm], self.params,
                                                     dest_uri=dest_uri, src_uri=self.src_uri)
            self.check_vm_cont_ping(check_on_dest=True)
        self.check_local_and_remote_log()

    def cleanup_default(self):
        """
        Cleanup steps by default

        """
        self.vm.connect_uri = self.src_uri

        dest_uri = self.params.get("virsh_migrate_desturi")
        set_remote_libvirtd_log = "yes" == self.params.get("set_remote_libvirtd_log", "no")

        self.test.log.debug("Recover test environment")
        if set_remote_libvirtd_log and self.remote_libvirtd_log:
            del self.remote_libvirtd_log
        # Clean VM on destination and source
        self.migration_test.cleanup_vm(self.vm, dest_uri)
        self.orig_config_xml.sync()

    def setup_connection(self):
        """
        Setup connection

        """
        transport_type = self.params.get("transport_type")
        transport_type_again = self.params.get("transport_type_again")
        migrate_desturi_port = self.params.get("migrate_desturi_port")
        migrate_desturi_type = self.params.get("migrate_desturi_type", "tcp")

        if migrate_desturi_type:
            self.conn_list.append(migration_base.setup_conn_obj(migrate_desturi_type, self.params, self.test))

        if transport_type and transport_type != migrate_desturi_type:
            self.conn_list.append(migration_base.setup_conn_obj(transport_type, self.params, self.test))

        if transport_type_again and transport_type_again not in [transport_type, migrate_desturi_type]:
            self.conn_list.append(migration_base.setup_conn_obj(transport_type_again, self.params, self.test))

        if migrate_desturi_port:
            self.remote_add_or_remove_port(migrate_desturi_port)
        self.setup_default()

    def cleanup_connection(self):
        """
        cleanup connection

        """
        migrate_desturi_port = self.params.get("migrate_desturi_port")

        self.cleanup_default()
        migration_base.cleanup_conn_obj(self.conn_list, self.test)
        if migrate_desturi_port:
            self.remote_add_or_remove_port(migrate_desturi_port, add=False)

    def set_remote_log(self):
        """
        Set remote libvirtd log file

        """
        log_level = self.params.get("libvirtd_debug_level")
        log_file = self.params.get("libvirtd_debug_file")
        log_filters = self.params.get("libvirtd_debug_filters")
        remote_file_type = self.params.get("remote_file_type")
        server_ip = self.params.get("server_ip")
        server_user = self.params.get("server_user")
        server_pwd = self.params.get("server_pwd")

        service_name = utils_libvirtd.Libvirtd(remote_file_type).service_name
        file_path = utils_config.get_conf_obj(service_name).conf_path
        self.test.log.debug("Config file path: %s" % file_path)
        cmd = "ls {0} || mkdir -p {0}".format(os.path.dirname(log_file))
        remote.run_remote_cmd(cmd, self.params, ignore_status=False)
        libvirtd_conf_dest = ('{".*log_level\s*=.*": "log_level = %s", '
                              '".*log_filters\s*=.*": \'log_filters="%s"\', '
                              '".*log_outputs\s*=.*": \'log_outputs="1:file:%s"\'}') % (log_level, log_filters, log_file)
        self.remote_libvirtd_log = libvirt_remote.update_remote_file(self.params, libvirtd_conf_dest, file_path)
        remote_runner = remote.RemoteRunner(host=server_ip,
                                            username=server_user,
                                            password=server_pwd)
        utils_libvirtd.Libvirtd(remote_file_type, session=remote_runner.session).restart()

    def check_local_and_remote_log(self, local_str_in_log=True, remote_str_in_log=True):
        """
        Check local and remote log file

        :param local_str_in_log: True if the local file should include the given string,
                                 otherwise, False
        :param remote_str_in_log: True if the remote file should include the given string,
                                  otherwise, False
        """
        try:
            check_str_local_log = eval(self.params.get("check_str_local_log", "[]"))
            check_no_str_local_log = eval(self.params.get("check_no_str_local_log", "[]"))
            check_str_remote_log = eval(self.params.get("check_str_remote_log", "[]"))
            check_no_str_remote_log = eval(self.params.get("check_no_str_remote_log", "[]"))
        except Exception as e:
            self.test.error(f"Wrong test configuration. Unable to eval one or more parameter(s): {str(e)}")
        log_file = self.params.get("libvirtd_debug_file")
        runner_on_target = None

        if check_str_local_log:
            for check_log in check_str_local_log:
                libvirt.check_logfile(check_log, log_file, str_in_log=local_str_in_log)
        if check_no_str_local_log:
            for check_log in check_no_str_local_log:
                local_str_in_log = False
                libvirt.check_logfile(check_log, log_file, str_in_log=local_str_in_log)
        if check_str_remote_log or check_no_str_remote_log:
            server_ip = self.params.get("server_ip")
            server_user = self.params.get("server_user", "root")
            server_pwd = self.params.get("server_pwd")
            runner_on_target = remote.RemoteRunner(host=server_ip,
                                                   username=server_user,
                                                   password=server_pwd)

        if check_str_remote_log:
            for check_log in check_str_remote_log:
                libvirt.check_logfile(check_log,
                                      log_file,
                                      str_in_log=remote_str_in_log,
                                      cmd_parms=self.params,
                                      runner_on_target=runner_on_target)
        if check_no_str_remote_log:
            remote_str_in_log = False
            for check_log in check_no_str_remote_log:
                libvirt.check_logfile(check_log,
                                      log_file,
                                      str_in_log=remote_str_in_log,
                                      cmd_parms=self.params,
                                      runner_on_target=runner_on_target)

    def remote_add_or_remove_port(self, port, add=True):
        """
        Add or remove port on remote host

        :param port: port
        :param add: True for add port, False for remove port
        """
        server_ip = self.params.get("server_ip")
        server_user = self.params.get("server_user")
        server_pwd = self.params.get("server_pwd")
        remote_session = remote.wait_for_login('ssh', server_ip, '22',
                                               server_user, server_pwd,
                                               r"[\#\$]\s*$")
        firewall_cmd = utils_iptables.Firewall_cmd(remote_session)
        if add:
            firewall_cmd.add_port(port, 'tcp', permanent=True)
        else:
            firewall_cmd.remove_port(port, 'tcp', permanent=True)
        # Wait for 2 seconds to make the firewall take effect
        time.sleep(2)
        remote_session.close()


def setup_network_data_transport(params):
    """
    Setup for network data transport

    """
    network_data_transport = params.get("network_data_transport")
    extra = params.get("virsh_migrate_extra")

    if network_data_transport and network_data_transport == "tls":
        extra = "--tls %s" % extra
        params.update({"virsh_migrate_extra": extra})


def recreate_conn_objs(params):
    """
    Recreate conn object

    :param params: dict, get migration object and transport type
    """
    transport_type = params.get("transport_type")
    migration_obj = params.get("migration_obj")

    migration_base.cleanup_conn_obj(migration_obj.conn_list, migration_obj.test)
    migration_obj.conn_list.append(migration_base.setup_conn_obj(transport_type, params, migration_obj.test))
    time.sleep(3)


def prepare_disks_remote(params, vm):
    """
    Prepare disks on target host

    :param params: dict, get server ip, server user and server password
    :param vm: vm object
    """
    server_ip = params.get("server_ip")
    server_user = params.get("server_user")
    server_pwd = params.get("server_pwd")

    remote_session = remote.remote_login("ssh", server_ip, "22",
                                         server_user, server_pwd,
                                         r'[$#%]')

    all_vm_disks = vm.get_blk_devices()
    for disk in list(itervalues(all_vm_disks)):
        disk_type = disk.get("type")
        disk_path = disk.get("source")
        image_info = utils_misc.get_image_info(disk_path)
        disk_size = image_info.get("vsize")
        disk_format = image_info.get("format")
        utils_misc.make_dirs(os.path.dirname(disk_path), remote_session)
        libvirt_disk.create_disk(disk_type, path=disk_path,
                                 size=disk_size, disk_format=disk_format,
                                 session=remote_session)
    remote_session.close()


def cleanup_disks_remote(params, vm):
    """
    Cleanup disks on target host

    :param params: Dictionary with the test parameters
    :param vm: vm object
    """
    all_vm_disks = vm.get_blk_devices()
    for disk in list(itervalues(all_vm_disks)):
        disk_path = disk.get("source")
        cmd = "rm -f %s" % disk_path
        remote.run_remote_cmd(cmd, params, ignore_status=False)


def sync_cpu_for_mig(params):
    """
    Sync cpu xml for migration

    :param params: Dictionary with the test parameters
    """
    dest_uri = params.get("virsh_migrate_desturi")
    vm_name = params.get("main_vm")

    cpu_dest_xml = virsh.domcapabilities(uri=dest_uri)
    cpu_src_xml = virsh.domcapabilities()
    mig_cpu_xml = os.path.join(data_dir.get_tmp_dir(), "cpu_xml")
    with open(mig_cpu_xml, 'w+') as fd:
        fd.write(cpu_dest_xml.stdout.strip())
        fd.write(cpu_src_xml.stdout.strip())
    out = virsh.hypervisor_cpu_baseline(mig_cpu_xml, options="--migratable")
    dom_xml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    cpuxml = vm_xml.VMCPUXML()
    cpuxml.xml = out.stdout.strip()
    dom_xml.cpu = cpuxml
    dom_xml.sync()


def check_cpu_for_mig(params):
    """
    Check cpu for migration

    :param params: Dictionary with the test parameters
    :return: if the cpu on the source and target hosts are the same, return True
    """
    dest_uri = params.get("virsh_migrate_desturi")
    remote_ip = params.get("server_ip")
    remote_user = params.get("server_user")
    remote_pwd = params.get("server_pwd")

    # aarch64 only supports migration tests between hosts with the same CPU, so
    # no need to check the CPU.
    if platform.machine() == "aarch64":
        return True

    cpu_src_xml = virsh.domcapabilities(debug=True).stdout_text.strip()
    mig_src_xml = os.path.join(data_dir.get_tmp_dir(), "mig_src.xml")
    with open(mig_src_xml, 'w+') as fd:
        fd.write(cpu_src_xml)

    remote_session = remote.remote_login("ssh", remote_ip, "22",
                                         remote_user, remote_pwd,
                                         r'[$#%]')
    utils_misc.make_dirs(os.path.dirname(mig_src_xml), remote_session)
    remote_session.close()
    remote.scp_to_remote(remote_ip, '22', remote_user, remote_pwd, mig_src_xml,
                         mig_src_xml, limit="", log_filename=None, timeout=60,
                         interface=None)

    ret = virsh.hypervisor_cpu_compare(xml_file=mig_src_xml, uri=dest_uri, debug=True).stdout_text.strip()
    if "identical" in ret or "superset" in ret:
        return True
    else:
        return False
