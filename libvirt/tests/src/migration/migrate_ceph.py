import logging
import os
import re

from avocado.core import exceptions
from avocado.core import data_dir
from avocado.utils import process

from virttest import ssh_key
from virttest import data_dir
from virttest import remote
from virttest import utils_package
from virttest import utils_selinux
from virttest import utils_package
from virttest import utils_conn
from virttest import virsh
from virttest import virt_vm
from virttest import migration

from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml import secret_xml
from virttest.libvirt_xml.devices.disk import Disk

from virttest.utils_test import libvirt

from virttest import libvirt_version

MIGRATE_RET = False


def check_output(test, output_msg, params):
    """
    Check if known messages exist in the given output messages.

    :param test: the test object
    :param output_msg: the given output messages
    :param params: the dictionary including necessary parameters

    :raise TestSkipError: raised if the known error is found together
                          with some conditions satisfied
    """
    err_msg = params.get("err_msg", None)
    status_error = params.get("status_error", "no")
    if status_error == "yes" and err_msg:
        if err_msg in output_msg:
            logging.debug("Expected error '%s' was found", err_msg)
            return
        else:
            test.fail("The expected error '%s' was not found in output '%s'" % (err_msg, output_msg))

    ERR_MSGDICT = {"Bug 1249587": "error: Operation not supported: " +
                   "pre-creation of storage targets for incremental " +
                   "storage migration is not supported",
                   "ERROR 1": "error: internal error: unable to " +
                   "execute QEMU command 'migrate': " +
                   "this feature or command is not currently supported",
                   "ERROR 2": "error: Cannot access storage file",
                   "ERROR 3": "Unable to read TLS confirmation: " +
                   "Input/output error",
                   "ERROR 4": "error: Unsafe migration: Migration " +
                   "without shared storage is unsafe"}

    # Check for special case firstly
    migrate_disks = "yes" == params.get("migrate_disks")
    status_error = "yes" == params.get("status_error")
    if migrate_disks and status_error:
        logging.debug("To check for migrate-disks...")
        disk = params.get("attach_A_disk_source")
        last_msg = "(as uid:107, gid:107): No such file or directory"
        if not libvirt_version.version_compare(4, 5, 0):
            expect_msg = "%s '%s' %s" % (ERR_MSGDICT["ERROR 2"],
                                         disk,
                                         last_msg)
        else:
            expect_msg = ERR_MSGDICT["ERROR 4"]
        if output_msg.find(expect_msg) >= 0:
            logging.debug("The expected error '%s' was found", expect_msg)
            return
        else:
            test.fail("The actual output:\n%s\n"
                      "The expected error '%s' was not found"
                      % (output_msg, expect_msg))

    if params.get("target_vm_name"):
        if output_msg.find(ERR_MSGDICT['ERROR 3']) >= 0:
            logging.debug("The expected error is found: %s", ERR_MSGDICT['ERROR 3'])
            return
        else:
            test.fail("The actual output:\n%s\n"
                      "The expected error '%s' was not found"
                      % (output_msg, ERR_MSGDICT['ERROR 3']))

    for (key, value) in ERR_MSGDICT.items():
        if output_msg.find(value) >= 0:
            if key == "ERROR 1" and params.get("support_precreation") is True:
                logging.debug("The error is not expected: '%s'.", value)
            elif key == "ERROR 2":
                break
            else:
                logging.debug("The known error was found: %s --- %s",
                              key, value)
                test.cancel("Known error: %s --- %s in %s"
                            % (key, value, output_msg))


def check_virsh_command_and_option(test, command, option=None):
    """
    Check if virsh command exists

    :param test: test object
    :param command: the command to validate
    :param option: the option for the command
    :raise: test.cancel if commmand is not supported
    """
    msg = "This version of libvirt does not support "
    if not virsh.has_help_command(command):
        test.cancel(msg + "virsh command '%s'" % command)

    if option and not virsh.has_command_help_match(command, option):
        test.cancel(msg + "virsh command '%s' with option '%s'"
                    % (command, option))


def migrate_vm(test, params):
    """
    Connect libvirt daemon

    :param test: the test object
    :param params: parameters used
    :raise: test.fail if migration does not get expected result
    """
    vm_name = params.get("vm_name_to_migrate")
    if vm_name is None:
        vm_name = params.get("main_vm", "")
    uri = params.get("desuri")
    options = params.get("virsh_options", "--live --verbose")
    extra = params.get("extra_args", "")
    su_user = params.get("su_user", "")
    auth_user = params.get("server_user")
    auth_pwd = params.get("server_pwd")
    virsh_patterns = params.get("patterns_virsh_cmd", r".*100\s%.*")
    status_error = params.get("status_error", "no")
    timeout = int(params.get("migration_timeout", 30))
    extra_opt = params.get("extra_opt", "")

    for option in options.split():
        if option.startswith("--"):
            check_virsh_command_and_option(test, "migrate", option)

    logging.info("Prepare migrate %s", vm_name)
    global MIGRATE_RET
    MIGRATE_RET, mig_output = libvirt.do_migration(vm_name, uri, extra,
                                                   auth_pwd, auth_user,
                                                   options,
                                                   virsh_patterns,
                                                   su_user, timeout,
                                                   extra_opt)

    if status_error == "no":
        if MIGRATE_RET:
            logging.info("Get an expected migration result:\n%s" % mig_output)
        else:
            check_output(test, mig_output, params)
            test.fail("Can't get an expected migration result:\n%s"
                      % mig_output)
    else:
        if not MIGRATE_RET:
            check_output(test, mig_output, params)
            logging.info("It's an expected error:\n%s" % mig_output)
        else:
            test.fail("Unexpected return result:\n%s" % mig_output)


def check_parameters(test, params):
    """
    Make sure all of parameters are assigned a valid value

    :param test: the test object
    :param params: parameters used
    :raise: test.cancel if not enough parameters are specified
    """
    client_ip = params.get("client_ip")
    server_ip = params.get("server_ip")
    ipv6_addr_src = params.get("ipv6_addr_src")
    ipv6_addr_des = params.get("ipv6_addr_des")
    client_cn = params.get("client_cn")
    server_cn = params.get("server_cn")
    client_ifname = params.get("client_ifname")
    server_ifname = params.get("server_ifname")

    args_list = [client_ip, server_ip, ipv6_addr_src,
                 ipv6_addr_des, client_cn, server_cn,
                 client_ifname, server_ifname]

    for arg in args_list:
        if arg and arg.count("ENTER.YOUR."):
            test.cancel("Please assign a value for %s!" % arg)


def get_secret_list(session=None):
    """
    Get secret list by virsh secret-list from local or remote host.

    :param session: virsh shell session.
    :return secret list
    """
    logging.info("Get secret list ...")
    if session:
        secret_list_result = session.secret_list()
    else:
        secret_list_result = virsh.secret_list()
    secret_list = secret_list_result.stdout_text.strip().splitlines()
    # First two lines contain table header followed by entries
    # for each secret, such as:
    #
    # UUID                                  Usage
    # --------------------------------------------------------------------------------
    # b4e8f6d3-100c-4e71-9f91-069f89742273  ceph client.libvirt secret
    secret_list = secret_list[2:]
    result = []
    # If secret list is empty.
    if secret_list:
        for line in secret_list:
            # Split on whitespace, assume 1 column
            linesplit = line.split(None, 1)
            result.append(linesplit[0])
    return result


def prepare_ceph_disk(ceph_params, remote_virsh_dargs, test, runner_on_target):
    """
    Prepare one image on remote ceph server with enabled or disabled auth
    And expose it to VM by network access

    :param ceph_params: parameter to setup ceph.
    :param remote_virsh_dargs: parameter to remote virsh.
    :param test: test itself.
    """
    # Ceph server config parameters
    virsh_dargs = {'debug': True, 'ignore_status': True}
    prompt = ceph_params.get("prompt", r"[\#\$]\s*$")
    ceph_disk = "yes" == ceph_params.get("ceph_disk")
    mon_host = ceph_params.get('mon_host')
    client_name = ceph_params.get('client_name')
    client_key = ceph_params.get("client_key")
    vol_name = ceph_params.get("vol_name")
    disk_img = ceph_params.get("disk_img")
    key_file = ceph_params.get("key_file")
    disk_format = ceph_params.get("disk_format")
    key_opt = ""

    # Auth and secret config parameters.
    auth_user = ceph_params.get("auth_user")
    auth_key = ceph_params.get("auth_key")
    auth_type = ceph_params.get("auth_type")
    auth_usage = ceph_params.get("secret_usage")
    secret_uuid = ceph_params.get("secret_uuid")

    # Remote host parameters.
    remote_ip = ceph_params.get("server_ip")
    remote_user = ceph_params.get("server_user", "root")
    remote_pwd = ceph_params.get("server_pwd")

    # Clean up dirty secrets in test environments if there are.
    dirty_secret_list = get_secret_list()
    if dirty_secret_list:
        for dirty_secret_uuid in dirty_secret_list:
            virsh.secret_undefine(dirty_secret_uuid)

    # Install ceph-common package which include rbd command
    if utils_package.package_install(["ceph-common"]):
        if client_name and client_key:
            # Clean up dirty secrets on remote host.
            try:
                remote_virsh = virsh.VirshPersistent(**remote_virsh_dargs)
                remote_dirty_secret_list = get_secret_list(remote_virsh)
                for dirty_secret_uuid in remote_dirty_secret_list:
                    remote_virsh.secret_undefine(dirty_secret_uuid)
            except (process.CmdError, remote.SCPError) as detail:
                raise exceptions.TestError(detail)
            finally:
                logging.debug('clean up secret on remote host')
                remote_virsh.close_session()

            with open(key_file, 'w') as f:
                f.write("[%s]\n\tkey = %s\n" %
                        (client_name, client_key))
            key_opt = "--keyring %s" % key_file

            # Create secret xml
            sec_xml = secret_xml.SecretXML("no", "no")
            sec_xml.usage = auth_type
            sec_xml.usage_name = auth_usage
            sec_xml.uuid = secret_uuid
            sec_xml.xmltreefile.write()

            logging.debug("Secret xml: %s", sec_xml)
            ret = virsh.secret_define(sec_xml.xml)
            libvirt.check_exit_status(ret)

            secret_uuid = re.findall(r".+\S+(\ +\S+)\ +.+\S+",
                                     ret.stdout.strip())[0].lstrip()
            logging.debug("Secret uuid %s", secret_uuid)
            if secret_uuid is None:
                test.fail("Failed to get secret uuid")

            # Set secret value
            ret = virsh.secret_set_value(secret_uuid, auth_key,
                                         **virsh_dargs)
            libvirt.check_exit_status(ret)

            # Create secret on remote host.
            local_path = sec_xml.xml
            remote_path = '/var/lib/libvirt/images/new_secret.xml'
            remote_folder = '/var/lib/libvirt/images'
            cmd = 'mkdir -p %s && chmod 777 %s && touch %s' % (remote_folder, remote_folder, remote_path)
            cmd_result = remote.run_remote_cmd(cmd, ceph_params, runner_on_target)
            status, output = cmd_result.exit_status, cmd_result.stdout_text.strip()

            if status:
                test.fail("Failed to run '%s' on the remote: %s"
                          % (cmd, output))
            remote.scp_to_remote(remote_ip, '22', remote_user,
                                 remote_pwd, local_path, remote_path,
                                 limit="", log_filename=None,
                                 timeout=600, interface=None)
            cmd = "/usr/bin/virsh secret-define --file %s" % remote_path
            cmd_result = remote.run_remote_cmd(cmd, ceph_params, runner_on_target)
            status, output = cmd_result.exit_status, cmd_result.stdout_text.strip()
            if status:
                test.fail("Failed to run '%s' on the remote: %s"
                          % (cmd, output))

            # Set secret value on remote host.
            cmd = "/usr/bin/virsh secret-set-value --secret %s --base64 %s" % (secret_uuid, auth_key)
            cmd_result = remote.run_remote_cmd(cmd, ceph_params, runner_on_target)
            status, output = cmd_result.exit_status, cmd_result.stdout_text.strip()

            if status:
                test.fail("Failed to run '%s' on the remote: %s"
                          % (cmd, output))

        # Delete the disk if it exists
        disk_src_name = "%s/%s" % (vol_name, disk_img)
        cmd = ("rbd -m {0} {1} info {2} && rbd -m {0} {1} rm "
               "{2}".format(mon_host, key_opt, disk_src_name))
        process.run(cmd, ignore_status=True, shell=True)

        # Convert the disk format
        first_disk_device = ceph_params.get('first_disk')
        blk_source = first_disk_device['source']
        disk_path = ("rbd:%s:mon_host=%s" %
                     (disk_src_name, mon_host))
        if auth_user and auth_key:
            disk_path += (":id=%s:key=%s" %
                          (auth_user, auth_key))
        disk_cmd = ("rbd -m %s %s info %s || qemu-img convert"
                    " -O %s %s %s" % (mon_host, key_opt,
                                      disk_src_name, disk_format,
                                      blk_source, disk_path))
        process.run(disk_cmd, ignore_status=False, shell=True)
        return (key_opt, secret_uuid)


def build_disk_xml(vm_name, disk_format, host_ip, disk_src_protocol,
                   volume_name, disk_img=None, transport=None, auth=None):
    """
    Try to build disk xml

    :param vm_name: specified VM name.
    :param disk_format: disk format,e.g raw or qcow2
    :param host_ip: host ip address
    :param disk_src_protocol: access disk procotol ,e.g network or file
    :param volume_name: volume name
    :param disk_img: disk image name
    :param transport: transport pattern,e.g TCP
    :param auth: dict containing ceph parameters
    """
    # Delete existed disks first.
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    disks_dev = vmxml.get_devices(device_type="disk")
    for disk in disks_dev:
        vmxml.del_device(disk)

    disk_xml = Disk(type_name="network")
    driver_dict = {"name": "qemu",
                   "type": disk_format,
                   "cache": "none"}
    disk_xml.driver = driver_dict
    disk_xml.target = {"dev": "vda", "bus": "virtio"}
    # If protocol is rbd,create ceph disk xml.
    if disk_src_protocol == "rbd":
        disk_xml.device = "disk"
        vol_name = volume_name
        source_dict = {"protocol": disk_src_protocol,
                       "name": "%s/%s" % (vol_name, disk_img)}
        host_dict = {"name": host_ip, "port": "6789"}
        if transport:
            host_dict.update({"transport": transport})
        if auth:
            disk_xml.auth = disk_xml.new_auth(**auth)
        disk_xml.source = disk_xml.new_disk_source(
            **{"attrs": source_dict, "hosts": [host_dict]})
    # Add the new disk xml.
    vmxml.add_device(disk_xml)
    vmxml.sync()


def run(test, params, env):
    """
    Test remote access with TCP, TLS connection
    """
    test_dict = dict(params)
    vm_name = test_dict.get("main_vm")
    vm = env.get_vm(vm_name)
    start_vm = test_dict.get("start_vm", "no")

    # Server and client parameters
    server_ip = test_dict.get("server_ip")
    server_user = test_dict.get("server_user")
    server_pwd = test_dict.get("server_pwd")
    client_ip = test_dict.get("client_ip")
    client_user = test_dict.get("client_user")
    client_pwd = test_dict.get("client_pwd")
    server_cn = test_dict.get("server_cn")
    client_cn = test_dict.get("client_cn")
    target_ip = test_dict.get("target_ip", "")
    # generate remote IP
    if target_ip == "":
        if server_cn:
            target_ip = server_cn
        elif server_ip:
            target_ip = server_ip
        else:
            target_ip = target_ip
    remote_virsh_dargs = {'remote_ip': server_ip, 'remote_user': server_user,
                          'remote_pwd': server_pwd, 'unprivileged_user': None,
                          'ssh_remote_auth': True}

    # Ceph disk parameters
    driver = test_dict.get("test_driver", "qemu")
    transport = test_dict.get("transport")
    plus = test_dict.get("conn_plus", "+")
    source_type = test_dict.get("vm_disk_source_type", "file")
    virsh_options = test_dict.get("virsh_options", "--verbose --live")
    vol_name = test_dict.get("vol_name")
    disk_src_protocol = params.get("disk_source_protocol")
    source_file = test_dict.get("disk_source_file")
    disk_format = test_dict.get("disk_format", "qcow2")
    mon_host = params.get("mon_host")
    ceph_key_opt = ""
    attach_disk = False
    # Disk XML file
    disk_xml = None
    # Define ceph_disk conditional variable
    ceph_disk = "yes" == test_dict.get("ceph_disk")

    # For --postcopy enable
    postcopy_options = test_dict.get("postcopy_options")
    if postcopy_options and not virsh_options.count(postcopy_options):
        virsh_options = "%s %s" % (virsh_options, postcopy_options)
        test_dict['virsh_options'] = virsh_options

    # For bi-directional and tls reverse test
    uri_port = test_dict.get("uri_port", ":22")
    uri_path = test_dict.get("uri_path", "/system")
    src_uri = test_dict.get("migration_source_uri", "qemu:///system")
    uri = "%s%s%s://%s%s%s" % (driver, plus, transport,
                               target_ip, uri_port, uri_path)
    test_dict["desuri"] = uri

    # Make sure all of parameters are assigned a valid value
    check_parameters(test, test_dict)
    # Set up SSH key

    #ssh_key.setup_ssh_key(server_ip, server_user, server_pwd, port=22)
    remote_session = remote.wait_for_login('ssh', server_ip, '22',
                                           server_user, server_pwd,
                                           r"[\#\$]\s*$")
    remote_session.close()
    #ssh_key.setup_ssh_key(server_ip, server_user, server_pwd, port=22)

    # Set up remote ssh key and remote /etc/hosts file for bi-direction migration
    migrate_vm_back = "yes" == test_dict.get("migrate_vm_back", "no")
    if migrate_vm_back:
        ssh_key.setup_remote_ssh_key(server_ip, server_user, server_pwd)
        ssh_key.setup_remote_known_hosts_file(client_ip,
                                              server_ip,
                                              server_user,
                                              server_pwd)
    # Reset Vm state if needed
    if vm.is_alive() and start_vm == "no":
        vm.destroy(gracefully=False)

    # Back up xml file.
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    # Setup migration context
    migrate_setup = migration.MigrationTest()
    migrate_setup.migrate_pre_setup(test_dict["desuri"], params)

    # Install ceph-common on remote host machine.
    remote_ssh_session = remote.remote_login("ssh", server_ip, "22", server_user,
                                             server_pwd, r"[\#\$]\s*$")
    if not utils_package.package_install(["ceph-common"], remote_ssh_session):
        test.error("Failed ot install required packages on remote host")
    remote_ssh_session.close()
    try:
        # Create a remote runner for later use
        runner_on_target = remote.RemoteRunner(host=server_ip,
                                               username=server_user,
                                               password=server_pwd)
        # Get initial Selinux config flex bit
        LOCAL_SELINUX_ENFORCING_STATUS = utils_selinux.get_status()
        logging.info("previous local enforce :%s", LOCAL_SELINUX_ENFORCING_STATUS)
        cmd_result = remote.run_remote_cmd('getenforce', params, runner_on_target)
        REMOTE_SELINUX_ENFORCING_STATUS = cmd_result.stdout_text
        logging.info("previous remote enforce :%s", REMOTE_SELINUX_ENFORCING_STATUS)

        if ceph_disk:
            logging.info("Put local SELinux in permissive mode when test ceph migrating")
            utils_selinux.set_status("enforcing")

            logging.info("Put remote SELinux in permissive mode")
            cmd = "setenforce enforcing"
            cmd_result = remote.run_remote_cmd(cmd, params, runner_on_target)
            status, output = cmd_result.exit_status, cmd_result.stdout_text.strip()
            if status:
                test.Error("Failed to set SELinux "
                           "in permissive mode")

            # Prepare ceph disk.
            key_file = os.path.join(data_dir.get_tmp_dir(), "ceph.key")
            test_dict['key_file'] = key_file
            test_dict['first_disk'] = vm.get_first_disk_devices()
            ceph_key_opt, secret_uuid = prepare_ceph_disk(test_dict, remote_virsh_dargs, test, runner_on_target)
            host_ip = test_dict.get('mon_host')
            disk_image = test_dict.get('disk_img')

            # Build auth information.
            auth_attrs = {}
            auth_attrs['auth_user'] = params.get("auth_user")
            auth_attrs['secret_type'] = params.get("secret_type")
            auth_attrs['secret_uuid'] = secret_uuid
            build_disk_xml(vm_name, disk_format, host_ip, disk_src_protocol,
                           vol_name, disk_image, auth=auth_attrs)

            vm_xml_cxt = process.run("virsh dumpxml %s" % vm_name, shell=True).stdout_text
            logging.debug("The VM XML with ceph disk source: \n%s", vm_xml_cxt)
            try:
                if vm.is_dead():
                    vm.start()
            except virt_vm.VMStartError as e:
                logging.info("Failed to start VM")
                test.fail("Failed to start VM: %s" % vm_name)

        # Ensure the same VM name doesn't exist on remote host before migrating.
        destroy_vm_cmd = "virsh destroy %s" % vm_name
        remote.run_remote_cmd(cmd, params, runner_on_target)

        # Trigger migration
        migrate_vm(test, test_dict)

        if migrate_vm_back:
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
            status, output = cmd_result.exit_status, cmd_result.stdout_text.strip()
            logging.info(output)
            if status:
                destroy_cmd = "virsh destroy %s" % vm_name
                remote.run_remote_cmd(destroy_cmd, params, runner_on_target)
                test.fail("Failed to run '%s' on remote: %s"
                          % (cmd, output))
    finally:
        logging.info("Recovery test environment")
        # Clean up of pre migration setup for local machine
        if migrate_vm_back:
            migrate_setup.migrate_pre_setup(src_uri, params,
                                            cleanup=True)
        # Ensure VM can be cleaned up on remote host even migrating fail.
        destroy_vm_cmd = "virsh destroy %s" % vm_name
        remote.run_remote_cmd(destroy_vm_cmd, params, runner_on_target)

        logging.info("Recovery VM XML configration")
        vmxml_backup.sync()
        logging.debug("The current VM XML:\n%s", vmxml_backup.xmltreefile)

        # Clean up ceph environment.
        if disk_src_protocol == "rbd":
            # Clean up secret
            secret_list = get_secret_list()
            if secret_list:
                for secret_uuid in secret_list:
                    virsh.secret_undefine(secret_uuid)
            # Clean up dirty secrets on remote host if testing involve in ceph auth.
            client_name = test_dict.get('client_name')
            client_key = test_dict.get("client_key")
            if client_name and client_key:
                try:
                    remote_virsh = virsh.VirshPersistent(**remote_virsh_dargs)
                    remote_dirty_secret_list = get_secret_list(remote_virsh)
                    for dirty_secret_uuid in remote_dirty_secret_list:
                        remote_virsh.secret_undefine(dirty_secret_uuid)
                except (process.CmdError, remote.SCPError) as detail:
                    test.Error(detail)
                finally:
                    remote_virsh.close_session()
            # Delete the disk if it exists.
            disk_src_name = "%s/%s" % (vol_name, test_dict.get('disk_img'))
            cmd = ("rbd -m {0} {1} info {2} && rbd -m {0} {1} rm "
                   "{2}".format(mon_host, ceph_key_opt, disk_src_name))
            process.run(cmd, ignore_status=True, shell=True)

        if LOCAL_SELINUX_ENFORCING_STATUS:
            logging.info("Restore SELinux in original mode")
            utils_selinux.set_status(LOCAL_SELINUX_ENFORCING_STATUS)
        if REMOTE_SELINUX_ENFORCING_STATUS:
            logging.info("Put remote SELinux in original mode")
            cmd = "yes yes | setenforce %s" % REMOTE_SELINUX_ENFORCING_STATUS
            remote.run_remote_cmd(cmd, params, runner_on_target)

        # Remove known hosts on local host
        cmd = "ssh-keygen -R  %s" % server_ip
        process.run(cmd, ignore_status=True, shell=True)

        # Remove known hosts on remote host
        cmd = "ssh-keygen -R  %s" % client_ip
        remote.run_remote_cmd(cmd, params, runner_on_target)
