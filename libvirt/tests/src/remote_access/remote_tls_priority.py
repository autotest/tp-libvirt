from virttest import virsh
from virttest import remote
from avocado.utils import process
from virttest import utils_iptables
from virttest import utils_libvirtd
from virttest import utils_config
from virttest import remote as remote_old
from virttest.utils_conn import TLSConnection
from virttest.utils_test.libvirt import remotely_control_libvirtd
from virttest.utils_test.libvirt import customize_libvirt_config


def change_parameter_on_remote(connect_params, replacement_dict):
    """
    Change required parameters based on replacement_dict in remote file
    defined in connect_params as remote_daemon_conf.

    :param connect_params. Dictionary with connection parameters like server_ip
    :param replacement_dict. Dictionary with pattern/replacement pairs
    :return: RemoteFile object.
    """
    remote_libvirtdconf = remote_old.RemoteFile(
        address=connect_params.get('server_ip'),
        client='scp',
        username=connect_params.get('server_user'),
        password=connect_params.get('server_pwd'),
        port=connect_params.get('port'),
        remote_path=connect_params.get('remote_daemon_conf'),
        verbose=True,
    )
    remote_libvirtdconf.sub_else_add(replacement_dict)
    return remote_libvirtdconf


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
                                      config_object=None)
    process.system('cat {}'.format(utils_config.LibvirtConfig().conf_path),
                   ignore_status=True,
                   shell=True)
    return config


def connect_to_server_hypervisor(params_to_connect_dict):
    """
    Connect to hypervisor via virsh.

    :param params_to_connect_dict. Dictionary with connection parameters like uri
    :return: True if successful or False otherwise.
    """
    out = virsh.command(params_to_connect_dict['cmd'],
                        uri=params_to_connect_dict['uri'],
                        debug=True,
                        ignore_status=True)
    # Look for expected output and return True if pass
    for pass_message in params_to_connect_dict['pass_message_list']:
        if params_to_connect_dict['expect_error']:
            if pass_message in out.stderr_text:
                return True
        else:
            if pass_message in out.stdout_text:
                return True
    return False


def create_session_on_remote(connect_params):
    """
    Connect to hypervisor via virsh.

    :param connect_params. Dictionary with connection parameters like server_ip.
    :return: session to server.
    """
    server_session = remote.wait_for_login('ssh',
                                           connect_params['server_ip'],
                                           connect_params['port'],
                                           connect_params['server_user'],
                                           connect_params['server_pwd'],
                                           r"[\#\$]\s*$")
    return server_session


def run(test, params, env):
    """
    Test TLS connection priorities with remote machine.
    """
    server_ip = params.get("server_ip")
    server_user = params.get("server_user")
    server_pwd = params.get("server_pwd")
    tls_port = params.get("tls_port", "16514")
    ssl_v3 = params.get("priority_ssl_v3_only")
    tls_v1 = params.get("priority_tls_v1_only")
    ssl_inv = params.get("priority_ssl_invalid")
    tls_inv = params.get("priority_tls_invalid")
    no_ssl_v3 = params.get("priority_no_ssl_v3")
    wrong_message = params.get("wrong_priorities_message").split(',')
    invalid_message = params.get("invalid_priorities_message").split(',')
    welcome_message = params.get("successful_message").split(',')
    uri = "qemu+tls://{}"
    remote_update_session = None

    # Initial Setup - this part is the longest and always required part of the
    # test and therefore it is not effective to divide this test into smaller
    # parts that would take much longer in sum.

    # Create connection with the server
    server_session = create_session_on_remote(params)
    # Make sure the Libvirtd is running on remote
    remote_libvirtd = utils_libvirtd.Libvirtd(session=server_session)
    if not remote_libvirtd.is_running():
        res = remote_libvirtd.start()
        if not res:
            status, output = server_session.cmd_status_output("journalctl -xe")
            test.error("Failed to start libvirtd on remote. [status]: %s "
                       "[output]: %s." % (status, output))

    # setup TLS
    tls_obj = TLSConnection(params)
    # setup test environment
    tls_obj.conn_setup()

    # Open the tls/tcp listening port on server
    firewall_cmd = utils_iptables.Firewall_cmd(server_session)
    firewall_cmd.add_port(tls_port, 'tcp', permanent=True)
    server_session.close()

    # Update TLS priority on remote
    replacements = {
        r'#tls_priority=".*?"': 'tls_priority="{}"'.format(
            params['remote_tls_priority'])
    }
    # DO NOT DELETE remote_update_session as it destroys changes applied by the
    # method remote_libvirtdconf.sub_else_add - cleanup called when the instance
    # is destroyed
    remote_update_session = change_parameter_on_remote(params,
                                                       replacements)
    # Restart Remote Libvirtd "
    remotely_control_libvirtd(server_ip, server_user, server_pwd, 'restart')

    config = None
    try:
        # Phase I - test via libvirt.conf on client

        # TLS priority set to SSLv3 only for client via libvirt.conf
        new_tls_priority = {'tls_priority': '"{}"'.format(ssl_v3)}
        config = change_libvirtconf_on_client(new_tls_priority)

        # Connect to hypervisor
        uri_path = server_ip + '/system'
        connect_dict = {'uri': uri.format(uri_path),
                        'cmd': "",
                        'pass_message_list': wrong_message,
                        'expect_error': True,
                        }
        test_pass = connect_to_server_hypervisor(connect_dict)
        # Restore if pass
        if test_pass:
            config.restore()
            config = None
        else:
            test.fail('TLS priorities test failed for case when client supports'
                      ' SSLv3 only and server does not support SSLv3 only.')

        # TLS priority set to TLS1.0 only for client via libvirt.conf
        new_tls_priority = {'tls_priority': '"{}"'.format(tls_v1)}
        config = change_libvirtconf_on_client(new_tls_priority)
        # Remove SSLv3.0 support via URI
        uri_path = server_ip + '/system' + '?tls_priority={}'.format(no_ssl_v3)
        connect_dict['uri'] = uri.format(uri_path)
        connect_dict['pass_message_list'] = welcome_message
        connect_dict['expect_error'] = False
        test_pass = connect_to_server_hypervisor(connect_dict)
        if test_pass:
            config.restore()
            config = None
        else:
            test.fail('TLS priorities test failed for case when client supports'
                      ' TLSv1.0 only and server does not support SSLv3 only.')
        # Pass invalid SSL priority
        new_tls_priority = {'tls_priority': '"{}"'.format(ssl_inv)}
        config = change_libvirtconf_on_client(new_tls_priority)
        uri_path = server_ip + '/system'
        connect_dict['uri'] = uri.format(uri_path)
        connect_dict['pass_message_list'] = invalid_message
        connect_dict['expect_error'] = True
        test_pass = connect_to_server_hypervisor(connect_dict)
        if test_pass:
            config.restore()
            config = None
        else:
            test.fail('TLS priorities test failed for case when client supports'
                      ' SSLv4.0 which is invalid.')

        # Phase II - test via URI on client
        uri_path = server_ip + '/system' + '?tls_priority={}'.format(ssl_v3)
        connect_dict['uri'] = uri.format(uri_path)
        connect_dict['pass_message_list'] = wrong_message
        connect_dict['expect_error'] = True
        test_pass = connect_to_server_hypervisor(connect_dict)
        if not test_pass:
            test.fail('TLS priorities test failed for case when the client '
                      'supports SSLv3 only by URI and the server does not '
                      'support SSLv3 only.')

        uri_path = server_ip + '/system' + '?tls_priority={}'.format(tls_v1)
        connect_dict['uri'] = uri.format(uri_path)
        connect_dict['pass_message_list'] = welcome_message
        connect_dict['expect_error'] = False
        test_pass = connect_to_server_hypervisor(connect_dict)
        if not test_pass:
            test.fail('TLS priorities test failed for case when the client '
                      'supports TLSv1.0 only by URI and the server does not '
                      'support SSLv3 only.')

        uri_path = server_ip + '/system' + '?tls_priority={}'.format(tls_inv)
        connect_dict['uri'] = uri.format(uri_path)
        connect_dict['pass_message_list'] = invalid_message
        connect_dict['expect_error'] = True
        test_pass = connect_to_server_hypervisor(connect_dict)
        if not test_pass:
            test.fail('TLS priorities test failed for case when the client '
                      'supports SSLv4.0 by URI which is invalid.')

    finally:
        # Reset Firewall
        server_session = create_session_on_remote(params)
        firewall_cmd = utils_iptables.Firewall_cmd(server_session)
        firewall_cmd.remove_port(tls_port, 'tcp')
        server_session.close()
        if config:
            config.restore()
        # Restore config on remote
        if remote_update_session:
            del remote_update_session
