import logging as log
import os

import aexpect
from aexpect import remote

from avocado.core import exceptions
from avocado.utils import process

from virttest import data_dir
from virttest import libvirt_version
from virttest import utils_iptables
from virttest import utils_libvirtd
from virttest import utils_misc
from virttest import utils_split_daemons
from virttest import remote as remote_old
from virttest.utils_test import libvirt


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def get_server_details(params):
    """
   Get the server details from the configuration parameters

   :param params: avocado params object
   :returns: required server information
   """
    server_info = {'ip': params.get('server_ip'),
                   'user': params.get('server_user'),
                   'pwd': params.get('server_pwd')}
    return server_info


def get_client_details(params):
    """
    Get the client details from the configuration parameters

    :param params: avocado params object
    :returns: required client information
    """
    client_info = {'ip': params.get('client_ip'),
                   'user': params.get('client_user'),
                   'pwd': params.get('client_pwd')}
    return client_info


def prepare_a_certs_dictionary(server_info):
    """
    Prepare a dictionary with the required information for info files and
    certificates.

    :param server_info: dictionary with the server information
    :returns: dictionary with required information
    """
    caroot = {'caroot': {'info': ['cn = Libvirt Root CA', 'ca',
                                  'cert_signing_key'],
                         'ca_cert': '',
                         }
              }
    cachild1 = {'cachild1': {'info': ['cn = Libvirt Child CA 1', 'ca',
                                      'cert_signing_key'],
                             'ca_cert': 'caroot',
                             }
                }
    cachild2 = {'cachild2': {'info': ['cn = Libvirt Child CA 2', 'ca',
                                      'cert_signing_key'],
                             'ca_cert': 'caroot',
                             }
                }
    server1 = {'server1': {'info': ['organization = Red Hat',
                                    'cn = host1.example.com',
                                    'dns_name = host1.example.com',
                                    'ip_address = ' + server_info['ip'],
                                    'tls_www_server', 'encryption_key',
                                    'signing_key'],
                           'ca_cert': 'cachild1',
                           }
               }
    server2 = {'server2': {'info': ['organization = Red Hat',
                                    'cn = host2.example.com', 'tls_www_server',
                                    'encryption_key', 'signing_key'],
                           'ca_cert': 'cachild2',
                           }
               }
    client1 = {'client1': {'info': ['country = GB', 'state = London',
                                    'locality = London',
                                    'organization = Red Hat', 'cn = client1',
                                    'tls_www_client', 'encryption_key',
                                    'signing_key'],
                           'ca_cert': 'cachild1',
                           }
               }
    client2 = {'client2': {'info': ['country = GB', 'state = London',
                                    'locality = London',
                                    'organization = Red Hat', 'cn = client2',
                                    'tls_www_client', 'encryption_key',
                                    'signing_key'],
                           'ca_cert': 'cachild2',
                           }
               }

    certs_dict = {**caroot, **cachild1, **cachild2, **server1, **server2,
                  **client1, **client2}

    return certs_dict


def prepare_info_files(certs_dict, dir_path):
    """
    Prepare info files based on information provided

    :param certs_dict: dictionary with the required certificates information
    :param dir_path: path pointing to required info files destination
    :returns: None
    """
    for name in certs_dict:
        info_file = name+'.info'
        with open(os.path.join(dir_path, info_file),  'w') as info_file:
            for line in certs_dict[name]['info']:
                info_file.write(line+'\n')


def generate_keys(certs_dir):
    """
    Generate keys based on info files

    :param certs_dir: path pointing to directory with certificates
    :returns: None
    """
    for name in os.listdir(certs_dir):
        try:
            basename, extension = name.split('.')
        except ValueError:
            continue
        if extension == 'info':
            cert_name = basename + 'key.pem'
            cmd = "certtool --generate-privkey --outfile={}".\
                format(os.path.join(certs_dir, cert_name))
            logging.debug('Command to generate keys: {}'.format(cmd))
            process.run(cmd, shell=True)


def generate_certificates(certs_dict, certs_dir):
    """
    Generate certificates from the information provided

    :param certs_dict: dictionary with the required certificates information
    :param certs_dir: path pointing to directory with certificates
    :returns: None
    """
    cwd = os.getcwd()
    os.chdir(certs_dir)
    for name in certs_dict:
        cmd = 'certtool {generate_keyword} --load-privkey {name}key.pem ' \
              '{cacert} {ca_private_key} --template {name}.info --outfile ' \
              '{name}cert.pem'.\
            format(generate_keyword='--generate-certificate' if certs_dict[name]['ca_cert'] else '--generate-self-signed',
                   name=name,
                   cacert=' --load-ca-certificate '+certs_dict[name]['ca_cert']+'cert.pem' if certs_dict[name]['ca_cert'] else '',
                   ca_private_key='--load-ca-privkey '+certs_dict[name]['ca_cert']+'key.pem' if certs_dict[name]['ca_cert'] else '',
                   )
        logging.debug('Command to generate certificate:\n{}'.format(cmd))
        process.run(cmd, shell=True)
    os.chdir(cwd)


def concatenate_certificates(certs_dir, *certificates):
    """
    Concatenate certificates chain into one CA certificate

    :param certs_dir: path pointing to directory with certificates
    :param certificates: multiple certificate names in required order
    :returns: None
    """
    cwd = os.getcwd()
    os.chdir(certs_dir)
    cacert_filename = 'cacert.pem'
    if not os.path.exists(cacert_filename):
        process.run('touch {}'.format(cacert_filename), shell=True)
    cert_string = ''
    for cert in certificates:
        cert_string += cert + ' '
    cmd = "cat {}> {}".format(cert_string, cacert_filename)
    process.run(cmd, shell=True)
    os.chdir(cwd)


def copy_ca_certs_to_hosts(certs_dir, *host_info):
    """
    Copy certificates to required destination path

    :param certs_dir: path pointing to directory with certificates
    :param host_info: multiple dictionaries with the host information
    :returns: path to destination CA certificate
    """
    ca_cert_path = os.path.join(certs_dir, 'cacert.pem')
    remote_ca_cert_path = '/etc/pki/CA/cacert.pem'
    try:
        for host in host_info:
            remote.copy_files_to(host['ip'], 'scp', host['user'],
                                 host['pwd'], '22', ca_cert_path,
                                 remote_ca_cert_path)
    except remote.SCPError as detail:
        raise exceptions.TestError(detail)
    return remote_ca_cert_path


def prepare_certs_and_keys_on_host(session, host, certs_dir, key_name):
    """
    Prepare certificates and keys on the host

    :param session: RemoteSession object for host connection
    :param host: dictionary with the host information
    :param certs_dir: path pointing to directory with certificates
    :param key_name: string with a name used for a key and certificate
    :returns: tuple of paths for key and certificate
    """
    libvirt_pki_private_dir = '/etc/pki/libvirt/private'
    libvirt_pki_dir = '/etc/pki/libvirt'
    cmd = "mkdir -p {}".format(libvirt_pki_private_dir)
    status, output = session.cmd_status_output(cmd)
    logging.debug("Making directory for certificates has failed due to: {}".
                  format(output))
    src_key_path = os.path.join(certs_dir, key_name + 'key.pem')
    src_cert_path = os.path.join(certs_dir, key_name + 'cert.pem')
    dest_key_path = os.path.join(libvirt_pki_private_dir,
                                 key_name[:-1] + 'key.pem')
    dest_cert_path = os.path.join(libvirt_pki_dir,
                                  key_name[:-1] + 'cert.pem')
    # SCP server cert and server key to server
    remote.copy_files_to(host['ip'], 'scp', host['user'], host['pwd'],
                         '22', src_key_path, dest_key_path)
    remote.copy_files_to(host['ip'], 'scp', host['user'], host['pwd'],
                         '22', src_cert_path, dest_cert_path)
    return dest_key_path, dest_cert_path


def get_server_syslibvirtd(server_info):
    """"
    Get the RemoteFile object of the syslibvirtd file

    :param server_info: dictionary with the server information
    :returns: RemoteFile object of the syslibvirtd file
    """
    syslibvirtd = remote_old.RemoteFile(
        address=server_info['ip'],
        client='scp',
        username=server_info['user'],
        password=server_info['pwd'],
        port='22',
        remote_path='/etc/sysconfig/libvirtd')
    return syslibvirtd


def get_daemon_configs():
    """
    Get the daemon configs

    :returns: daemon configs file path
    """
    if utils_split_daemons.is_modular_daemon():
        daemon_conf = "/etc/libvirt/virtproxyd.conf"
        daemon_socket_conf = "/usr/lib/systemd/system/virtproxyd-tls.socket"
    else:
        daemon_conf = "/etc/libvirt/libvirtd.conf"
        daemon_socket_conf = "/usr/lib/systemd/system/libvirtd-tls.socket"
    return daemon_conf, daemon_socket_conf


def get_server_libvirtdconf(server_info):
    """
    Get the RemoteFile object of the libvirtdconf file

    :param server_info: dictionary with the server information
    :returns: RemoteFile object of the libvirtdconf file
    """
    daemon_conf, _daemon_socket_conf = get_daemon_configs()
    server_libvirtdconf = remote_old.RemoteFile(
        address=server_info['ip'],
        client='scp',
        username=server_info['user'],
        password=server_info['pwd'],
        port='22',
        remote_path=daemon_conf)
    return server_libvirtdconf


def restart_libvirtd_on_server(session):
    """
    Restart libvirtd service(s) on the remote server to apply changes

    :param session: RemoteSession object for server connection
    :returns: None
    """
    if libvirt_version.version_compare(5, 6, 0, session):
        tls_socket_service = utils_libvirtd.DaemonSocket(
            "virtproxyd-tls.socket", session=session)
        tls_socket_service.restart()
    else:
        libvirtd_service = utils_libvirtd.Libvirtd(
            session=session)
        libvirtd_service.restart()


def setup_libvirt_on_server(server_session, server_info):
    """
    Setup libvirtd on remote server to allow TLS connection.

    :param server_session: RemoteSession object for server connection
    :param server_info: dictionary with the server information
    :returns: tuple of the RemoteFile objects with libvirtdconf and syslibvirtd
    """
    libvirtdconf = get_server_libvirtdconf(server_info)
    syslibvirtd = None
    if not libvirt_version.version_compare(5, 6, 0, server_session):
        syslibvirtd = get_server_syslibvirtd(server_info)
        # edit the /etc/sysconfig/libvirtd to add --listen args in libvirtd
        pattern_to_repl = {
            r".*LIBVIRTD_ARGS\s*=\s*\"\s*--listen\s*\".*":
                "LIBVIRTD_ARGS=\"--listen\""
        }
        syslibvirtd.sub_else_add(pattern_to_repl)
        # edit the /etc/libvirt/libvirtd.conf to add listen_tls=1
        pattern_to_repl = {r".*listen_tls\s*=\s*.*": "listen_tls=1"}
        libvirtdconf.sub_else_add(pattern_to_repl)

    pattern_to_repl = {r".*auth_tls\s*=\s*.*": 'auth_tls="none"'}
    libvirtdconf.sub_else_add(pattern_to_repl)

    try:
        restart_libvirtd_on_server(server_session)
    except (remote.LoginError, aexpect.ShellError) as detail:
        raise exceptions.TestError(detail)
    return libvirtdconf, syslibvirtd


def stop_iptables():
    """
    Clear iptables to make sure no rule prevents connection

    :returns: None
    """
    cmd = "iptables -F"
    process.run(cmd, shell=True)


def allow_port_in_fw(server_session):
    """
    Allow the libvirt TLS port in the firewall on the remote server

    :param server_session: RemoteSession object for server connection
    :returns: None
    """
    firewalld_port = '16514'
    firewall_cmd = utils_iptables.Firewall_cmd(server_session)
    firewall_cmd.add_port(firewalld_port, 'tcp', permanent=True)


def connect_to_remote(server_info, err_msg=None):
    """
    Try connection to the remote server with TLS

    :param server_info: dictionary with the server information
    :param err_msg: expected error messages (if any)
    :returns: None
    """
    expected_fails = [err_msg] if err_msg else []
    result = process.run('virsh -c qemu+tls://{}/system'.
                         format(server_info['ip']), shell=True,
                         ignore_status=True)
    libvirt.check_result(result, expected_fails=expected_fails,
                         check_both_on_error=True)


def get_log(server_info):
    """
    Tail output appended data as the file /var/log/messages grows

    :param server_info: dictionary with the server information
    :returns: the appended data tailed from /var/log/messages
    """
    tailed_log_file = os.path.join(data_dir.get_tmp_dir(), 'tail_log')
    tail_session = remote.remote_login('ssh', server_info['ip'], '22',
                                       server_info['user'],
                                       server_info['pwd'],
                                       r"[\#\$]\s*$",
                                       log_function=utils_misc.log_line,
                                       log_filename=tailed_log_file)
    tail_session.sendline('tail -f /var/log/messages')
    return tail_session


def run(test, params, env):
    """
    Test remote access with TLS connection and multiple CA certificates
    """
    config_files = []
    server_files = []
    client_files = []
    ca_cert_file = None

    server_info = get_server_details(params)
    server_session = remote.wait_for_login('ssh', server_info['ip'], '22',
                                           server_info['user'],
                                           server_info['pwd'],
                                           r"[\#\$]\s*$")
    client_info = get_client_details(params)
    client_session = remote.wait_for_login('ssh', client_info['ip'], '22',
                                           client_info['user'],
                                           client_info['pwd'],
                                           r"[\#\$]\s*$")
    try:
        # NOTE: The Test can be divided to multiple parts, however the first
        # part - setup is a time consuming and it is therefore better to do it
        # once only.
        certs_dict = prepare_a_certs_dictionary(server_info)
        certs_dir = os.getcwd()

        prepare_info_files(certs_dict, certs_dir)
        generate_keys(certs_dir)
        generate_certificates(certs_dict, certs_dir)
        concatenate_certificates(certs_dir,
                                 'carootcert.pem',
                                 'cachild1cert.pem',
                                 'cachild2cert.pem')
        ca_cert_file = copy_ca_certs_to_hosts(certs_dir,
                                              server_info,
                                              client_info)
        server_files = prepare_certs_and_keys_on_host(server_session,
                                                      server_info,
                                                      certs_dir,
                                                      'server1')
        config_files = setup_libvirt_on_server(server_session, server_info)
        stop_iptables()
        allow_port_in_fw(server_session)
        restart_libvirtd_on_server(server_session)
        client_files = prepare_certs_and_keys_on_host(client_session,
                                                      client_info,
                                                      certs_dir,
                                                      'client1')
        # Connect to server1 hypervisor on client1
        connect_to_remote(server_info)

        # Test with other CA certificates order
        for new_order in [
            ['cachild2cert.pem', 'carootcert.pem', 'cachild1cert.pem'],
            ['cachild1cert.pem', 'carootcert.pem', 'cachild2cert.pem'],
        ]:
            concatenate_certificates(certs_dir, *new_order)
            copy_ca_certs_to_hosts(certs_dir, server_info, client_info)
            restart_libvirtd_on_server(server_session)
            connect_to_remote(server_info)

        # Test with missing issuing CA
        concatenate_certificates(certs_dir,
                                 'cachild2cert.pem',
                                 'carootcert.pem')
        # Publish to server only
        copy_ca_certs_to_hosts(certs_dir, server_info)
        # Start reading the /var/log/messages on server
        tail_messages = get_log(server_info)
        restart_libvirtd_on_server(server_session)
        err_msg = params.get('err_msg')
        output = tail_messages.get_output()
        tail_messages.close()
        if err_msg not in output:
            test.fail("Unexpected output of the /var/log/messages on remote "
                      "server: {}".format(output))
        # Fix the CA certificates
        concatenate_certificates(certs_dir,
                                 'cachild2cert.pem',
                                 'carootcert.pem',
                                 'cachild1cert.pem')
        # Copy to server
        copy_ca_certs_to_hosts(certs_dir, server_info)
        restart_libvirtd_on_server(server_session)
        # Check if the connection can be established again
        connect_to_remote(server_info)
        # Create an invalid CA cert for client
        concatenate_certificates(certs_dir,
                                 'cachild2cert.pem',
                                 'carootcert.pem')
        # Copy to client
        copy_ca_certs_to_hosts(certs_dir, client_info)
        connect_to_remote(server_info, err_msg)
    except Exception as e:
        test.fail('Unexpected failure: {}'.format(e))
    finally:
        if config_files:
            for config in config_files:
                del config
        if server_files:
            for file_path in server_files:
                server_session.cmd_status_output('rm -f {}'.format(file_path))
        if client_files:
            for file_path in client_files:
                client_session.cmd_status_output('rm -f {}'.format(file_path))
        if ca_cert_file:
            server_session.cmd_status_output('rm -f {}'.format(ca_cert_file))
            client_session.cmd_status_output('rm -f {}'.format(ca_cert_file))
