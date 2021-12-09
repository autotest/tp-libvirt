import logging
import os
import re
import shutil

from avocado.utils import process

from virttest import libvirt_vm
from virttest import data_dir
from virttest import utils_libvirtd
from virttest import virsh
from virttest import utils_conn

from virttest.utils_test import libvirt as utlv


def check_virsh_connect_alive(test, params):
    virsh_instance = virsh.VirshPersistent()
    try:
        virsh_instance.dom_list(debug=True)
        ret = virsh_instance.connect("test", debug=True, ignore_status=True)
        # Return value should be an error
        if ret.exit_status:
            # But, there is no output in stderr, all output is in stdout
            if not ret.stderr:
                # Therefore copy stdout to stderr to check by check_result
                ret.stderr = ret.stdout
        utlv.check_result(ret, expected_fails=[params['expected_result_patt']])
        virsh_instance.dom_list(debug=True)
        virsh_instance.connect(debug=True)
        virsh_instance.dom_list(debug=True)
    except Exception as e:
        test.fail('Unexpected failure: {}'.format(e))


def do_virsh_connect(uri, options):
    """
    Execute connect command in a virsh session and return the uri
    of this virsh session after connect.

    Raise a process.CmdError if execute virsh connect command failed.

    :param uri: argument of virsh connect command.
    :param options: options pass to command connect.

    :return: the uri of the virsh session after connect.

    """
    virsh_instance = virsh.VirshPersistent()
    virsh_instance.connect(uri, options)

    uri_result = virsh_instance.canonical_uri()
    del virsh_instance
    logging.debug("uri after connect is %s.", (uri_result))
    return uri_result


def run(test, params, env):
    """
    Test command: virsh connect.
    """
    def unix_transport_setup():
        """
        Setup a unix connect to local libvirtd.
        """
        shutil.copy(libvirtd_conf_path, libvirtd_conf_bak_path)

        with open(libvirtd_conf_path, 'r') as libvirtdconf_file:
            line_list = libvirtdconf_file.readlines()
        conf_dict = {r'auth_unix_rw\s*=': 'auth_unix_rw="none"\n', }
        for key in conf_dict:
            pattern = key
            conf_line = conf_dict[key]
            flag = False
            for index in range(len(line_list)):
                line = line_list[index]
                if not re.search(pattern, line):
                    continue
                else:
                    line_list[index] = conf_line
                    flag = True
                    break
            if not flag:
                line_list.append(conf_line)

        with open(libvirtd_conf_path, 'w') as libvirtdconf_file:
            libvirtdconf_file.writelines(line_list)

        # restart libvirtd service
        utils_libvirtd.libvirtd_restart()

    def unix_transport_recover():
        """
        Recover the libvirtd on local.
        """
        if os.path.exists(libvirtd_conf_bak_path):
            shutil.copy(libvirtd_conf_bak_path, libvirtd_conf_path)
            utils_libvirtd.libvirtd_restart()

    # get the params from subtests.
    # params for general.
    connect_arg = params.get("connect_arg", "")
    connect_opt = params.get("connect_opt", "")
    status_error = params.get("status_error", "no")

    # params for transport connect.
    local_ip = params.get("local_ip", "ENTER.YOUR.LOCAL.IP")
    local_pwd = params.get("local_pwd", "ENTER.YOUR.LOCAL.ROOT.PASSWORD")
    transport_type = params.get("connect_transport_type", "local")
    transport = params.get("connect_transport", "ssh")
    client_ip = local_ip
    client_pwd = local_pwd
    server_ip = local_ip
    server_pwd = local_pwd

    # params special for tls connect.
    server_cn = params.get("connect_server_cn", "TLSServer")
    client_cn = params.get("connect_client_cn", "TLSClient")

    # params special for tcp connect.
    tcp_port = params.get("tcp_port", '16509')

    # params special for unix transport.
    libvirtd_conf_path = '/etc/libvirt/libvirtd.conf'
    libvirtd_conf_bak_path = '%s/libvirtd.conf.bak' % data_dir.get_tmp_dir()

    # special params for test connection alive
    alive = params.get('alive', None)

    if alive:
        check_virsh_connect_alive(test, params)
        return

    # check the config
    if (connect_arg == "transport" and
            transport_type == "remote" and
            local_ip.count("ENTER")):
        test.cancel("Parameter local_ip is not configured"
                    "in remote test.")
    if (connect_arg == "transport" and
            transport_type == "remote" and
            local_pwd.count("ENTER")):
        test.cancel("Parameter local_pwd is not configured"
                    "in remote test.")
    # In Ubuntu libvirt_lxc available in /usr/lib/libvirt
    if (connect_arg.count("lxc") and
            (not (os.path.exists("/usr/libexec/libvirt_lxc") or
                  os.path.exists("/usr/lib/libvirt/libvirt_lxc")))):
        test.cancel("Connect test of lxc:/// is not suggested on "
                    "the host with no lxc driver.")
    if connect_arg.count("xen") and (not os.path.exists("/var/run/xend")):
        test.cancel("Connect test of xen:/// is not suggested on "
                    "the host with no xen driver.")
    if connect_arg.count("qemu") and (not os.path.exists("/dev/kvm")):
        test.cancel("Connect test of qemu:/// is not suggested"
                    "on the host with no qemu driver.")

    if connect_arg == "transport":
        canonical_uri_type = virsh.driver()

        if transport == "ssh":
            ssh_connection = utils_conn.SSHConnection(server_ip=server_ip,
                                                      server_pwd=server_pwd,
                                                      client_ip=client_ip,
                                                      client_pwd=client_pwd)
            try:
                ssh_connection.conn_check()
            except utils_conn.ConnectionError:
                ssh_connection.conn_setup()
                ssh_connection.conn_check()

            connect_uri = libvirt_vm.get_uri_with_transport(
                uri_type=canonical_uri_type,
                transport=transport, dest_ip=server_ip)
        elif transport == "tls":
            tls_connection = utils_conn.TLSConnection(server_ip=server_ip,
                                                      server_pwd=server_pwd,
                                                      client_ip=client_ip,
                                                      client_pwd=client_pwd,
                                                      server_cn=server_cn,
                                                      client_cn=client_cn,
                                                      special_cn='yes')
            tls_connection.conn_setup()

            connect_uri = libvirt_vm.get_uri_with_transport(
                uri_type=canonical_uri_type,
                transport=transport, dest_ip=server_cn)
        elif transport == "tcp":
            tcp_connection = utils_conn.TCPConnection(server_ip=server_ip,
                                                      server_pwd=server_pwd,
                                                      tcp_port=tcp_port)
            tcp_connection.conn_setup()

            connect_uri = libvirt_vm.get_uri_with_transport(
                uri_type=canonical_uri_type,
                transport=transport,
                dest_ip="%s:%s"
                % (server_ip, tcp_port))
        elif transport == "unix":
            unix_transport_setup()
            connect_uri = libvirt_vm.get_uri_with_transport(
                uri_type=canonical_uri_type,
                transport=transport,
                dest_ip="")
        else:
            test.cancel("Configuration of transport=%s is "
                        "not recognized." % transport)
    else:
        connect_uri = connect_arg

    try:
        try:
            uri = do_virsh_connect(connect_uri, connect_opt)
            # connect successfully
            if status_error == "yes":
                test.fail("Connect successfully in the "
                          "case expected to fail.")
            # get the expect uri when connect argument is ""
            if connect_uri == "":
                connect_uri = virsh.canonical_uri().split()[-1]

            logging.debug("expected uri is: %s", connect_uri)
            logging.debug("actual uri after connect is: %s", uri)
            if not uri == connect_uri:
                test.fail("Command exit normally but the uri is "
                          "not set as expected.")
        except process.CmdError as detail:
            if status_error == "no":
                test.fail("Connect failed in the case expected"
                          "to success.\n"
                          "Error: %s" % detail)
    finally:
        if transport == "unix":
            unix_transport_recover()
        if transport == "tcp":
            tcp_connection.conn_recover()
        if transport == "tls":
            tls_connection.conn_recover()
