import os
import aexpect

from virttest import migration
from virttest import libvirt_remote
from virttest import remote
from virttest import utils_iptables

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
        self.conn_list = []
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

        self.test.log.info("Setup steps by default.")
        if set_remote_libvirtd_log:
            self.set_remote_log()

        libvirt.set_vm_disk(self.vm, self.params)
        if not self.vm.is_alive():
            self.vm.start()
            self.vm.wait_for_login().close()

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
        extra = self.params.get("virsh_migrate_extra")
        extra_args = self.migration_test.update_virsh_migrate_extra_args(self.params)
        postcopy_options = self.params.get("postcopy_options")
        if postcopy_options:
            extra = "%s %s" % (extra, postcopy_options)

        # Check local guest network connection before migration
        self.migration_test.ping_vm(self.vm, self.params)
        self.test.log.debug("Guest xml after starting:\n%s",
                            vm_xml.VMXML.new_from_dumpxml(vm_name))

        if action_during_mig:
            action_during_mig = migration_base.parse_funcs(action_during_mig,
                                                           self.test, self.params)
        mode = 'both' if postcopy_options else 'precopy'
        if migrate_speed:
            self.migration_test.control_migrate_speed(vm_name, int(migrate_speed), mode)
        if stress_package:
            self.migration_test.run_stress_in_vm(self.vm, self.params)

        # Execute migration process
        migration_base.do_migration(self.vm, self.migration_test, None, dest_uri,
                                    options, virsh_options, extra,
                                    action_during_mig, extra_args)

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
        postcopy_options = self.params.get("postcopy_options")
        if postcopy_options:
            extra = "%s %s" % (extra, postcopy_options)

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

        mode = 'both' if postcopy_options else 'precopy'
        if migrate_speed_again:
            self.migration_test.control_migrate_speed(vm_name,
                                                      int(migrate_speed_again),
                                                      mode)

        migration_base.do_migration(self.vm, self.migration_test, None, dest_uri,
                                    options, virsh_options,
                                    extra, action_during_mig,
                                    extra_args)

    def verify_default(self):
        """
        Verify steps by default

        """
        dest_uri = self.params.get("virsh_migrate_desturi")
        vm_name = self.params.get("migrate_main_vm")

        aexpect.kill_tail_threads()
        func_returns = dict(self.migration_test.func_ret)
        self.migration_test.func_ret.clear()
        self.test.log.debug("Migration returns function results:%s", func_returns)
        if int(self.migration_test.ret.exit_status) == 0:
            self.migration_test.post_migration_check([self.vm], self.params,
                                                     dest_uri=dest_uri, src_uri=self.src_uri)
        self.check_local_or_remote_log()

    def cleanup_default(self):
        """
        Cleanup steps by default

        """
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
        extra = self.params.get("virsh_migrate_extra")
        uri_port = self.params.get("uri_port")

        if transport_type:
            self.conn_list.append(migration_base.setup_conn_obj(transport_type, self.params, self.test))

        if '--tls' in extra:
            self.conn_list.append(migration_base.setup_conn_obj('tls', self.params, self.test))

        if uri_port:
            self.remote_add_or_remove_port(uri_port)
        self.setup_default()

    def cleanup_connection(self):
        """
        cleanup connection

        """
        uri_port = self.params.get("uri_port")

        self.cleanup_default()
        migration_base.cleanup_conn_obj(self.conn_list, self.test)
        if uri_port:
            self.remote_add_or_remove_port(uri_port, add=False)

    def set_remote_log(self):
        """
        Set remote libvirtd log file

        """
        log_level = self.params.get("libvirtd_debug_level")
        log_file = self.params.get("libvirtd_debug_file")
        libvirtd_file_path = self.params.get("libvirtd_file_path")

        cmd = "ls {0} || mkdir -p {0}".format(os.path.dirname(log_file))
        remote.run_remote_cmd(cmd, self.params, ignore_status=False)
        libvirtd_conf_dest = ('{".*log_level\s*=.*": "log_level = %s", '
                              '".*log_outputs\s*=.*": \'log_outputs="1:file:%s"\'}') % (log_level, log_file)
        self.remote_libvirtd_log = libvirt_remote.update_remote_file(self.params, libvirtd_conf_dest, libvirtd_file_path)

    def check_local_and_remote_log(self, local_str_in_log=True, remote_str_in_log=True):
        """
        Check local and remote log file

        :param local_str_in_log: True if the local file should include the given string,
                                 otherwise, False
        :param remote_str_in_log: True if the remote file should include the given string,
                                  otherwise, False
        """
        check_str_local_log = self.params.get("check_str_local_log", "")
        check_str_remote_log = self.params.get("check_str_remote_log", "")
        log_file = self.params.get("libvirtd_debug_file")
        if check_str_local_log:
            libvirt.check_logfile(check_str_local_log, log_file, str_in_log=local_str_in_log)
        if check_str_remote_log:
            runner_on_target = None
            server_ip = self.params.get("server_ip")
            server_user = self.params.get("server_user", "root")
            server_pwd = self.params.get("server_pwd")
            runner_on_target = remote.RemoteRunner(host=server_ip,
                                                   username=server_user,
                                                   password=server_pwd)
            libvirt.check_logfile(check_str_remote_log,
                                  log_file,
                                  str_in_log=remote_str_in_log,
                                  cmd_parms=self.params,
                                  runner_on_target=runner_on_target)

    def remote_add_or_remove_port(self, uri_port, add=True):
        """
        Add or remove port on remote host

        :param uri_port: test object
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
            firewall_cmd.add_port(uri_port, 'tcp', permanent=True)
        else:
            firewall_cmd.remove_port(uri_port, 'tcp', permanent=True)
        remote_session.close()
