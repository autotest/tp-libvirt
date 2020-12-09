import os
import re
import logging

from avocado.utils import process

from virttest import libvirt_version
from virttest import utils_iptables
from virttest import virt_admin
from virttest import remote
from virttest.utils_conn import TLSConnection
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Test command: virt-admin server-update-tls.

    1) when tls related files changed, notify server to update TLS related files
        online, without restart deamon
    """
    def add_remote_firewall_port(port, params):
        """
        Add the port on remote host

        :param port: port to add
        :param params: Dictionary with the test parameters
        """
        server_ip = params.get("server_ip")
        server_user = params.get("server_user")
        server_pwd = params.get("server_pwd")
        remote_session = remote.wait_for_login('ssh', server_ip, '22',
                                               server_user, server_pwd,
                                               r"[\#\$]\s*$")
        firewall_cmd = utils_iptables.Firewall_cmd(remote_session)
        firewall_cmd.add_port(port, 'tcp', permanent=True)
        remote_session.close()

    def remove_remote_firewall_port(port, params):
        """
        Remove the port on remote host

        :param port: port to remove
        :param params: Dictionary with the test parameters
        """
        server_ip = params.get("server_ip")
        server_user = params.get("server_user")
        server_pwd = params.get("server_pwd")
        remote_session = remote.wait_for_login('ssh', server_ip, '22',
                                               server_user, server_pwd,
                                               r"[\#\$]\s*$")
        firewall_cmd = utils_iptables.Firewall_cmd(remote_session)
        firewall_cmd.remove_port(port, 'tcp', permanent=True)
        remote_session.close()

    def update_server_pem(cert_saved_dir, remote_libvirt_pki_dir):
        """
        Update the server info and re-build servercert

        :param cert_saved_dir: The directory where cert files are saved
        :param remote_libvirt_pki_dir: Directory to store pki on remote
        """
        logging.debug("Update serverinfo")
        serverinfo = os.path.join(cert_saved_dir, "server.info")
        with open(os.path.join(cert_saved_dir, "server.info"), "r") as f1:
            lines = f1.readlines()
        with open(os.path.join(cert_saved_dir, "server2.info"), "w") as f2:
            for line in lines:
                if fake_ip in line:
                    line = line.replace(fake_ip, server_ip)
                f2.write(line)

        cmd = ("certtool --generate-certificate --load-privkey "
               "{0}/serverkey.pem --load-ca-certificate {0}/cacert.pem "
               "--load-ca-privkey {0}/cakey.pem --template {0}/server2.info "
               "--outfile {0}/servercert.pem".format(cert_saved_dir))
        servercert_pem = os.path.join(cert_saved_dir, "servercert.pem")
        process.run(cmd, shell=True, verbose=True)
        remote.copy_files_to(server_ip, 'scp', server_user, server_pwd, '22',
                             servercert_pem, remote_libvirt_pki_dir)

    server_ip = params["server_ip"] = params.get("remote_ip")
    server_user = params["server_user"] = params.get("remote_user", "root")
    server_pwd = params["server_pwd"] = params.get("remote_pwd")
    client_ip = params["client_ip"] = params.get("local_ip")
    client_pwd = params["client_pwd"] = params.get("local_pwd")
    tls_port = params.get("tls_port", "16514")
    uri = "qemu+tls://%s:%s/system" % (server_ip, tls_port)

    remote_virt_dargs = {'remote_ip': server_ip, 'remote_user': server_user,
                         'remote_pwd': server_pwd, 'unprivileged_user': None,
                         'ssh_remote_auth': True}
    tls_obj = None

    if not libvirt_version.version_compare(6, 2, 0):
        test.cancel("This libvirt version doesn't support "
                    "virt-admin server-update-tls.")
    try:
        vp = virt_admin.VirtadminPersistent(**remote_virt_dargs)

        add_remote_firewall_port(tls_port, params)

        # Generate a fake ip for testing
        repl = str(int(server_ip.strip().split('.')[-1])+1 % 255)
        fake_ip = re.sub("([0-9]+)$", repl, server_ip)
        params.update({"server_info_ip": fake_ip})

        tls_obj = TLSConnection(params)
        tls_obj.conn_setup()
        tls_obj.auto_recover = True

        # Connection should fail because TLS is set incorrectly
        ret, output = libvirt.connect_libvirtd(uri)
        if ret:
            test.fail("Connection should fail but succeed. ret: {}, output: {}"
                      .format(ret, output))
        if "authentication failed" not in output:
            test.fail("Unablee to find the expected error message. output: %s"
                      % output)

        tmp_dir = tls_obj.tmp_dir
        remote_libvirt_pki_dir = tls_obj.libvirt_pki_dir
        update_server_pem(tmp_dir, remote_libvirt_pki_dir)

        serv_name = virt_admin.check_server_name()
        logging.debug("service name: %s", serv_name)
        result = vp.server_update_tls(serv_name, debug=True)
        libvirt.check_exit_status(result)

        # Re-connect to the server
        ret, output = libvirt.connect_libvirtd(uri)
        if not ret:
            test.fail("Connection fails, ret: {}, output: {}"
                      .format(ret, output))
    finally:
        logging.info("Recover test environment")
        remove_remote_firewall_port(tls_port, params)
