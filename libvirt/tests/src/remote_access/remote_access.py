import re
import os
import logging
import json

from avocado.utils import distro
from avocado.utils import process

from virttest import virsh
from virttest import remote
from virttest import utils_config
from virttest import utils_iptables

from virttest import libvirt_version
from virttest.utils_sasl import SASL
from virttest.utils_net import IPv6Manager
from virttest.utils_libvirtd import Libvirtd
from virttest.utils_conn import SSHConnection
from virttest.utils_conn import TCPConnection
from virttest.utils_conn import TLSConnection
from virttest.utils_conn import UNIXConnection
from virttest.utils_test.libvirt import connect_libvirtd
from virttest.utils_test.libvirt import customize_libvirt_config
from virttest.utils_test.libvirt import remotely_control_libvirtd
from virttest.utils_net import check_listening_port_remote_by_service


def remote_access(params, test):
    """
    Connect libvirt daemon
    """
    uri = params.get("uri")
    auth_user = params.get("auth_user", "root")
    auth_pwd = params.get("auth_pwd")
    virsh_cmd = params.get("virsh_cmd", "list")
    read_only = params.get("read_only", "")
    vm_name = params.get("main_vm", "")
    logfile = params.get("logfile")
    extra_env = params.get("extra_env", "")
    pattern = params.get("filter_pattern", "")
    su_user = params.get("su_user", "")
    virsh_patterns = params.get("patterns_virsh_cmd", r".*Id\s*Name\s*State\s*.*")
    patterns_extra_dict = params.get("patterns_extra_dict", None)
    log_level = params.get("log_level", "LIBVIRT_DEBUG=3")
    error_pattern = params.get("error_pattern", "")

    status_error = params.get("status_error", "no")
    ret, output = connect_libvirtd(uri, read_only, virsh_cmd, auth_user,
                                   auth_pwd, vm_name, status_error, extra_env,
                                   log_level, su_user, virsh_patterns,
                                   patterns_extra_dict)

    if status_error == "no":
        if ret:
            if pattern != "":
                fp = open(logfile, "r")
                if not re.findall(pattern, fp.read()):
                    fp.close()
                    test.fail("Failed to find %s in log!!" % pattern)
                fp.close()
            logging.info("Succeed to connect libvirt daemon.")
        else:
            test.fail("Failed to connect libvirt daemon!!output: {}"
                      .format(output))
    else:
        if not ret:
            if error_pattern:
                if error_pattern in output:
                    logging.info("Expected libvirt output!!")
                else:
                    test.fail("Unexpected error: {}, when trying to connect to "
                              "libvirt daemon. Looking for: {} pattern".
                              format(output, error_pattern))
            else:
                logging.info("It's an expected error!!")
        else:
            test.fail("Unexpected return result: {}".format(output))


def check_parameters(params, test):
    """
    Make sure all of parameters are assigned a valid value
    """
    client_ip = params.get("client_ip")
    server_ip = params.get("server_ip")
    ipv6_addr_src = params.get("ipv6_addr_src")
    ipv6_addr_des = params.get("ipv6_addr_des")
    auth_pwd = params.get("auth_pwd")
    uri_path = params.get("uri_path")
    client_cn = params.get("client_cn")
    server_cn = params.get("server_cn")
    client_ifname = params.get("client_ifname")
    server_ifname = params.get("server_ifname")

    args_list = [client_ip, server_ip, ipv6_addr_src, ipv6_addr_des,
                 auth_pwd, uri_path, client_cn, server_cn,
                 client_ifname, server_ifname]

    for arg in args_list:
        if arg and arg.count("ENTER.YOUR."):
            test.cancel("Please assign a value for %s!" % arg)


def compare_virt_version(server_ip, server_user, server_pwd, test):
    """
    Make sure libvirt version is different
    """
    client = "ssh"
    port = "22"
    prompt = r"[\#\$]\s*$"
    query_cmd = "rpm -q libvirt"
    # query libvirt version on local host
    ret = process.run(query_cmd, allow_output_check='combined', shell=True)
    status, output_local = ret.exit_status, ret.stdout_text.strip()
    if status:
        test.error(output_local)
    # query libvirt version on remote host
    session = remote.wait_for_login(client, server_ip, port,
                                    server_user, server_pwd, prompt)
    status, output_remote = session.cmd_status_output(query_cmd)
    if status:
        test.error(output_remote)
    # compare libvirt version between local and remote host
    if output_local == output_remote.strip():
        test.cancel("To expect different libvirt version <%s>:<%s>"
                    % (output_local, output_remote))


def cleanup(objs_list):
    """
    Clean up test environment
    """
    # recovery test environment
    objs_list.reverse()
    for obj in objs_list:
        obj.auto_recover = True
        obj.__del__()
        obj.auto_recover = False


def change_libvirtconf_on_client(params_to_change_dict):
    """
    Change required parameters based on params_to_change_dict in local
    libvirt.conf file.

    :param params_to_change_dict. Dictionary with name/replacement pairs
    :return: config object.
    """
    config = customize_libvirt_config(params_to_change_dict,
                                      config_type="libvirt",
                                      remote_host=False,
                                      extra_params=None,
                                      is_recover=False,
                                      config_object=None,
                                      restart_libvirt=False)
    process.system('cat {}'.format(utils_config.LibvirtConfig().conf_path),
                   ignore_status=False, shell=True)
    return config


def restore_libvirtconf_on_client(config):
    customize_libvirt_config({}, is_recover=True, config_type="libvirt",
                             config_object=config)


def run(test, params, env):
    """
    Test remote access with TCP, TLS connection
    """

    test_dict = dict(params)
    pattern = test_dict.get("filter_pattern", "")
    if ('@LIBVIRT' in pattern and
            distro.detect().name == 'rhel' and
            int(distro.detect().version) < 8):
        test.cancel("The test {} is not supported on current OS({}{}<8.0) as "
                    "the keyword @LIBVIRT is not supported by gnutls on this "
                    "OS.".format(test.name, distro.detect().name,
                                 distro.detect().version))
    vm_name = test_dict.get("main_vm")
    status_error = test_dict.get("status_error", "no")
    allowed_dn_str = params.get("tls_allowed_dn_list")
    if allowed_dn_str:
        allowed_dn_list = []
        if not libvirt_version.version_compare(1, 0, 0):
            # Reverse the order in the dn list to workaround the
            # feature changes between RHEL 6 and RHEL 7
            dn_list = allowed_dn_str.split(",")
            dn_list.reverse()
            allowed_dn_str = ','.join(dn_list)
        allowed_dn_list.append(allowed_dn_str)
        test_dict['tls_allowed_dn_list'] = allowed_dn_list
    transport = test_dict.get("transport")
    plus = test_dict.get("conn_plus", "+")
    config_ipv6 = test_dict.get("config_ipv6", "no")
    tls_port = test_dict.get("tls_port", "")
    listen_addr = test_dict.get("listen_addr", "0.0.0.0")
    ssh_port = test_dict.get("ssh_port", "")
    tcp_port = test_dict.get("tcp_port", "")
    server_ip = test_dict.get("server_ip")
    server_user = test_dict.get("server_user")
    server_pwd = test_dict.get("server_pwd")
    no_any_config = params.get("no_any_config", "no")
    sasl_type = test_dict.get("sasl_type", "gssapi")
    sasl_user_pwd = test_dict.get("sasl_user_pwd")
    sasl_allowed_users = test_dict.get("sasl_allowed_users")
    server_cn = test_dict.get("server_cn")
    custom_pki_path = test_dict.get("custom_pki_path")
    rm_client_key_cmd = test_dict.get("remove_client_key_cmd")
    rm_client_cert_cmd = test_dict.get("remove_client_cert_cmd")
    ca_cn_new = test_dict.get("ca_cn_new")
    no_verify = test_dict.get("no_verify", "no")
    ipv6_addr_des = test_dict.get("ipv6_addr_des")
    tls_sanity_cert = test_dict.get("tls_sanity_cert")
    restart_libvirtd = test_dict.get("restart_libvirtd", "yes")
    diff_virt_ver = test_dict.get("diff_virt_ver", "no")
    driver = test_dict.get("test_driver", "qemu")
    uri_path = test_dict.get("uri_path", "/system")
    virsh_cmd = params.get("virsh_cmd", "list")
    action = test_dict.get("libvirtd_action", "restart")
    uri_user = test_dict.get("uri_user", "")
    uri_aliases = test_dict.get("uri_aliases", "")
    uri_default = test_dict.get("uri_default", "")
    unix_sock_dir = test_dict.get("unix_sock_dir")
    mkdir_cmd = test_dict.get("mkdir_cmd")
    rmdir_cmd = test_dict.get("rmdir_cmd")
    adduser_cmd = test_dict.get("adduser_cmd")
    deluser_cmd = test_dict.get("deluser_cmd")
    auth_conf = test_dict.get("auth_conf")
    auth_conf_cxt = test_dict.get("auth_conf_cxt")
    polkit_pkla = test_dict.get("polkit_pkla")
    polkit_pkla_cxt = test_dict.get("polkit_pkla_cxt")
    ssh_setup = test_dict.get("ssh_setup", "no")
    tcp_setup = test_dict.get("tcp_setup", "no")
    tls_setup = test_dict.get("tls_setup", "no")
    unix_setup = test_dict.get("unix_setup", "no")
    ssh_recovery = test_dict.get("ssh_auto_recovery", "yes")
    tcp_recovery = test_dict.get("tcp_auto_recovery", "yes")
    tls_recovery = test_dict.get("tls_auto_recovery", "yes")
    unix_recovery = test_dict.get("unix_auto_recovery", "yes")
    sasl_allowed_username_list = test_dict.get("sasl_allowed_username_list")
    auth_unix_rw = test_dict.get("auth_unix_rw")
    kinit_pwd = test_dict.get("kinit_pwd")
    test_alias = test_dict.get("test_alias")

    config_list = []
    port = ""
    # extra URI arguments
    extra_params = ""
    # it's used to clean up SSH, TLS, TCP, UNIX and SASL objs later
    objs_list = []
    # redirect LIBVIRT_DEBUG log into test log later
    test_dict["logfile"] = test.logfile

    # Make sure all of parameters are assigned a valid value
    check_parameters(test_dict, test)
    # Make sure libvirtd on remote is running
    server_session = remote.wait_for_login('ssh', server_ip, '22',
                                           server_user, server_pwd,
                                           r"[\#\$]\s*$")

    remote_libvirtd = Libvirtd(session=server_session)
    if not remote_libvirtd.is_running():
        logging.debug("start libvirt on remote")
        res = remote_libvirtd.start()
        if not res:
            status, output = server_session.cmd_status_output("journalctl -xe")
            test.error("Failed to start libvirtd on remote. [status]: %s "
                       "[output]: %s." % (status, output))
    server_session.close()

    # only simply connect libvirt daemon then return
    if no_any_config == "yes":
        test_dict["uri"] = "%s%s%s://%s" % (driver, plus, transport, uri_path)
        remote_access(test_dict, test)
        return

    # append extra 'pkipath' argument to URI if exists
    if custom_pki_path:
        extra_params = "?pkipath=%s" % custom_pki_path

    # append extra 'no_verify' argument to URI if exists
    if no_verify == "yes":
        extra_params = "?no_verify=1"

    # append extra 'socket' argument to URI if exists
    if unix_sock_dir:
        extra_params = "?socket=%s/libvirt-sock" % unix_sock_dir

    # generate auth.conf and default under the '/etc/libvirt'
    if auth_conf_cxt and auth_conf:
        cmd = "echo -e '%s' > %s" % (auth_conf_cxt, auth_conf)
        process.system(cmd, ignore_status=True, shell=True)

    # generate polkit_pkla and default under the
    # '/etc/polkit-1/localauthority/50-local.d/'
    if polkit_pkla_cxt and polkit_pkla:
        cmd = "echo -e '%s' > %s" % (polkit_pkla_cxt, polkit_pkla)
        process.system(cmd, ignore_status=True, shell=True)

    # generate remote IP
    if config_ipv6 == "yes" and ipv6_addr_des:
        remote_ip = "[%s]" % ipv6_addr_des
    elif config_ipv6 != "yes" and server_cn:
        remote_ip = server_cn
    elif config_ipv6 != "yes" and ipv6_addr_des:
        remote_ip = "[%s]" % ipv6_addr_des
    elif server_ip and transport != "unix":
        remote_ip = server_ip
    else:
        remote_ip = ""

    # get URI port
    if tcp_port != "":
        port = ":" + tcp_port

    if tls_port != "":
        port = ":" + tls_port

    if ssh_port != "" and not ipv6_addr_des:
        port = ":" + ssh_port

    # generate URI
    uri = "%s%s%s://%s%s%s%s%s" % (driver, plus, transport, uri_user,
                                   remote_ip, port, uri_path, extra_params)
    test_dict["uri"] = uri

    logging.debug("The final test dict:\n<%s>", test_dict)

    if virsh_cmd == "start" and transport != "unix":
        session = remote.wait_for_login("ssh", server_ip, "22", "root",
                                        server_pwd, "#")
        cmd = "virsh domstate %s" % vm_name
        status, output = session.cmd_status_output(cmd)
        if status:
            session.close()
            test.cancel(output)

        session.close()

    try:
        # setup IPv6
        if config_ipv6 == "yes":
            ipv6_obj = IPv6Manager(test_dict)
            objs_list.append(ipv6_obj)
            ipv6_obj.setup()

        # compare libvirt version if needs
        if diff_virt_ver == "yes":
            compare_virt_version(server_ip, server_user, server_pwd, test)

        # setup SSH
        if (transport == "ssh" or ssh_setup == "yes") and sasl_type != "plain":
            if not test_dict.get("auth_pwd"):
                ssh_obj = SSHConnection(test_dict)
                if ssh_recovery == "yes":
                    objs_list.append(ssh_obj)
                # setup test environment
                ssh_obj.conn_setup()
            else:
                # To access to server with password,
                # cleanup authorized_keys on remote
                ssh_pubkey_file = "/root/.ssh/id_rsa.pub"
                if (os.path.exists("/root/.ssh/id_rsa") and
                        os.path.exists(ssh_pubkey_file)):
                    remote_file_obj = remote.RemoteFile(address=server_ip,
                                                        client='scp',
                                                        username=server_user,
                                                        password=server_pwd,
                                                        port='22',
                                                        remote_path="/root/.ssh/authorized_keys")
                    with open(ssh_pubkey_file, 'r') as fd:
                        line = fd.read().split()[-1].rstrip('\n')
                    line = ".*" + line
                    remote_file_obj.remove([line])
                    objs_list.append(remote_file_obj)

        # setup TLS
        if transport == "tls" or tls_setup == "yes":
            tls_obj = TLSConnection(test_dict)
            if tls_recovery == "yes":
                objs_list.append(tls_obj)
            # reserve cert path
            tmp_dir = tls_obj.tmp_dir
            # setup test environment
            tls_obj.conn_setup()

        # setup TCP
        if transport == "tcp" or tcp_setup == "yes":
            tcp_obj = TCPConnection(test_dict)
            if tcp_recovery == "yes":
                objs_list.append(tcp_obj)
            # setup test environment
            tcp_obj.conn_setup()

        # create a directory if needs
        if mkdir_cmd:
            process.system(mkdir_cmd, ignore_status=True, shell=True)

        # setup UNIX
        if transport == "unix" or unix_setup == "yes" or sasl_type == "plain":
            unix_obj = UNIXConnection(test_dict)
            if unix_recovery == "yes":
                objs_list.append(unix_obj)
            # setup test environment
            unix_obj.conn_setup()

        # need to restart libvirt service for negative testing
        if restart_libvirtd == "no":
            remotely_control_libvirtd(server_ip, server_user,
                                      server_pwd, action, status_error)
        # check TCP/IP listening by service
        if restart_libvirtd != "no" and transport != "unix":
            service = 'libvirtd'
            if transport == "ssh":
                service = 'ssh'

            check_listening_port_remote_by_service(server_ip, server_user,
                                                   server_pwd, service,
                                                   port, listen_addr)

        # open the tls/tcp listening port on server
        if transport in ["tls", "tcp"]:
            firewalld_port = port[1:]
            server_session = remote.wait_for_login('ssh', server_ip, '22',
                                                   server_user, server_pwd,
                                                   r"[\#\$]\s*$")
            firewall_cmd = utils_iptables.Firewall_cmd(server_session)
            firewall_cmd.add_port(firewalld_port, 'tcp', permanent=True)
            server_session.close()

        if 'inv_transport' in test_dict:
            transport = test_dict['inv_transport']
            uri = "%s%s%s://%s%s%s%s%s" % (driver, plus, transport, uri_user,
                                           remote_ip, port, uri_path,
                                           extra_params)
            test_dict["uri"] = uri

        config_list = []
        if uri_aliases:
            uri_config = change_libvirtconf_on_client(
                {'uri_aliases': uri_aliases})
            config_list.append(uri_config)
            test_dict["uri"] = test_alias

        if uri_default:
            test_dict["uri"] = ""
            # Delete the default URI environment variable to prevent overriding
            del os.environ['LIBVIRT_DEFAULT_URI']
            uri_config = change_libvirtconf_on_client(
                {'uri_default': uri_default})
            config_list.append(uri_config)
            ret = virsh.command('uri', debug=True)
            if uri_default.strip('"') in ret.stdout_text:
                logging.debug("Virsh output as expected.")
            else:
                test.fail("Expected virsh output: {} not found in:{}".format(
                    uri_default.strip('"'), ret.stdout_text))

        # remove client certifications if exist, only for TLS negative testing
        if rm_client_key_cmd:
            process.system(rm_client_key_cmd, ignore_status=True, shell=True)

        if rm_client_cert_cmd:
            process.system(rm_client_cert_cmd, ignore_status=True, shell=True)

        # add user to specific group
        if adduser_cmd:
            process.system(adduser_cmd, ignore_status=True, shell=True)

        # change /etc/pki/libvirt/servercert.pem then
        # restart libvirt service on the remote host
        if tls_sanity_cert == "no" and ca_cn_new:
            test_dict['ca_cn'] = ca_cn_new
            test_dict['scp_new_cacert'] = 'no'
            tls_obj_new = TLSConnection(test_dict)
            test_dict['tls_obj_new'] = tls_obj_new
            # only setup new CA and server
            tls_obj_new.conn_setup(True, False)

        # obtain and cache a ticket
        if kinit_pwd and sasl_type == 'gssapi' and auth_unix_rw == 'sasl':
            username_list = json.loads(sasl_allowed_username_list)
            for username in username_list:
                kinit_cmd = "echo '%s' | kinit %s" % (kinit_pwd, username)
                process.system(kinit_cmd, ignore_status=True, shell=True)

        # setup SASL certification
        # From libvirt-3.2.0, the default sasl change from
        # DIGEST-MD5 to GSSAPI. "sasl_user" is discarded.
        # More details: https://libvirt.org/auth.html#ACL_server_kerberos
        if sasl_user_pwd and sasl_type in ['digest-md5', 'plain']:
            # covert string tuple and list to python data type
            sasl_user_pwd = eval(sasl_user_pwd)
            if sasl_allowed_users:
                sasl_allowed_users = eval(sasl_allowed_users)

            # create a sasl user
            sasl_obj = SASL(test_dict)
            objs_list.append(sasl_obj)
            sasl_obj.setup()

            for sasl_user, sasl_pwd in sasl_user_pwd:
                # need't authentication if the auth.conf is configured by user
                if not auth_conf:
                    if sasl_type == 'plain':
                        test_dict["auth_pwd"] = server_pwd
                        pass
                    else:
                        test_dict["auth_user"] = sasl_user
                        test_dict["auth_pwd"] = sasl_pwd
                    logging.debug("sasl_user, sasl_pwd = "
                                  "(%s, %s)", sasl_user, sasl_pwd)

                if sasl_allowed_users and sasl_user not in sasl_allowed_users:
                    test_dict["status_error"] = "yes"
                patterns_extra_dict = {"authentication name": sasl_user,
                                       "enter your password": sasl_pwd}
                test_dict["patterns_extra_dict"] = patterns_extra_dict
                remote_access(test_dict, test)
        else:
            if not uri_default:
                remote_access(test_dict, test)

    finally:
        # recovery test environment
        # Destroy the VM after all test are done
        for config in config_list:
            restore_libvirtconf_on_client(config)
        cleanup(objs_list)

        if vm_name:
            vm = env.get_vm(vm_name)
            if vm and vm.is_alive():
                vm.destroy(gracefully=False)

        if transport in ["tcp", "tls"] and 'firewalld_port' in locals():
            server_session = remote.wait_for_login('ssh', server_ip, '22',
                                                   server_user, server_pwd,
                                                   r"[\#\$]\s*$")
            firewall_cmd = utils_iptables.Firewall_cmd(server_session)
            firewall_cmd.remove_port(firewalld_port, 'tcp', permanent=True)
            server_session.close()

        if rmdir_cmd:
            process.system(rmdir_cmd, ignore_status=True, shell=True)

        if deluser_cmd:
            process.system(deluser_cmd, ignore_status=True, shell=True)

        if auth_conf and os.path.isfile(auth_conf):
            os.unlink(auth_conf)

        if polkit_pkla and os.path.isfile(polkit_pkla):
            os.unlink(polkit_pkla)
