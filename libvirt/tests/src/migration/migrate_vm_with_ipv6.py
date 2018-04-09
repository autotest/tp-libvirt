import os
import logging
from virttest import remote
from autotest.client.shared import error
from virttest import nfs
from virttest.libvirt_xml import vm_xml
from virttest.utils_conn import SSHConnection, TCPConnection, \
    TLSConnection
from virttest.utils_net import IPv6Manager, \
    check_listening_port_remote_by_service

from virttest.utils_misc import SELinuxBoolean
from virttest.utils_test.libvirt import do_migration, update_vm_disk_source


def migrate_vm_with_ipv6(params):
    """
    Connect libvirt daemon
    """
    vm_name = params.get("main_vm", "")
    uri = params.get("desuri")
    options = params.get("virsh_options", "--verbose --live")
    extra = params.get("extra", "")
    status_error = params.get("status_error")
    auth_user = params.get("server_user")
    auth_pwd = params.get("server_pwd")
    virsh_patterns = params.get("patterns_virsh_cmd", ".*100\s%.*")

    status_error = params.get("status_error", "no")
    ret = do_migration(vm_name, uri, extra, auth_pwd,
                       auth_user, options, virsh_patterns)

    if status_error == "no":
        if ret:
            logging.info("Succeed to migrate VM.")
        else:
            raise error.TestFail("Failed to migrate VM!!")
    else:
        if not ret:
            logging.info("It's an expected error!!")
        else:
            raise error.TestFail("Unexpected return result!!")


def check_parameters(params):
    """
    Make sure all of parameters are assigned a valid value
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
            raise error.TestNAError("Please assign a value for %s!", arg)


def cleanup(objs_list):
    """
    Clean up test environment
    """
    # recovery test environment
    for obj in objs_list:
        obj.auto_recover = True
        del obj


def run(test, params, env):
    """
    Test remote access with TCP, TLS connection
    """

    test_dict = dict(params)
    vm_name = test_dict.get("main_vm")
    vm = env.get_vm(vm_name)
    status_error = test_dict.get("status_error", "no")
    transport = test_dict.get("transport")
    plus = test_dict.get("conn_plus", "+")
    config_ipv6 = test_dict.get("config_ipv6", "no")
    listen_addr = test_dict.get("listen_addr", "0.0.0.0")
    tls_port = test_dict.get("tls_port", "")
    ssh_port = test_dict.get("ssh_port", "")
    tcp_port = test_dict.get("tcp_port", "")
    server_ip = test_dict.get("server_ip")
    server_user = test_dict.get("server_user")
    server_pwd = test_dict.get("server_pwd")
    client_ip = test_dict.get("client_ip")
    server_cn = test_dict.get("server_cn")
    ipv6_addr_des = test_dict.get("ipv6_addr_des")
    restart_libvirtd = test_dict.get("restart_libvirtd", "yes")
    driver = test_dict.get("test_driver", "qemu")
    uri_path = test_dict.get("uri_path", "/system")
    nfs_mount_dir = test_dict.get("nfs_mount_dir")
    ssh_recovery = test_dict.get("ssh_auto_recovery", "yes")
    tcp_recovery = test_dict.get("tcp_auto_recovery", "yes")
    tls_recovery = test_dict.get("tls_auto_recovery", "yes")
    source_type = test_dict.get("vm_disk_source_type", "file")

    port = ""
    # it's used to clean up SSH, TLS and TCP objs later
    objs_list = []

    # Make sure all of parameters are assigned a valid value
    check_parameters(test_dict)

    # Back up xml file.
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    # Get the first disk source path
    first_disk = vm.get_first_disk_devices()
    disk_source = first_disk['source']
    logging.debug("disk source: %s", disk_source)

    # Update VM disk source to NFS sharing directory
    if nfs_mount_dir != os.path.dirname(disk_source):
        update_vm_disk_source(vm_name, nfs_mount_dir, source_type)

    logging.info("Setup NFS test environment...")
    nfs_serv = nfs.Nfs(test_dict)
    nfs_serv.setup()
    nfs_cli = nfs.NFSClient(test_dict)
    nfs_cli.setup()

    logging.info("Enable virt NFS SELinux boolean")
    se_obj = SELinuxBoolean(test_dict)
    se_obj.setup()

    # generate remote IP
    if config_ipv6 == "yes" and ipv6_addr_des:
        remote_ip = "[%s]" % ipv6_addr_des
    elif config_ipv6 != "yes" and server_cn:
        remote_ip = server_cn
    elif config_ipv6 != "yes" and ipv6_addr_des:
        remote_ip = "[%s]" % ipv6_addr_des
    elif server_ip:
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
    uri = "%s%s%s://%s%s%s" % (driver, plus, transport,
                               remote_ip, port, uri_path)
    test_dict["desuri"] = uri

    logging.debug("The final test dict:\n<%s>", test_dict)

    try:
        # setup IPv6
        if config_ipv6 == "yes":
            ipv6_obj = IPv6Manager(test_dict)
            objs_list.append(ipv6_obj)
            ipv6_obj.setup()

        # setup SSH
        if transport == "ssh":
            ssh_obj = SSHConnection(test_dict)
            if ssh_recovery == "yes":
                objs_list.append(ssh_obj)
            # setup test environment
            ssh_obj.conn_setup()

        # setup TLS
        if transport == "tls":
            tls_obj = TLSConnection(test_dict)
            if tls_recovery == "yes":
                objs_list.append(tls_obj)
            # setup CA, server and client
            tls_obj.conn_setup()

        # setup TCP
        if transport == "tcp":
            tcp_obj = TCPConnection(test_dict)
            if tcp_recovery == "yes":
                objs_list.append(tcp_obj)
            # setup test environment
            tcp_obj.conn_setup()

        # check TCP/IP listening by service
        if restart_libvirtd != "no":
            service = 'libvirtd'
            if transport == "ssh":
                service = 'ssh'

            check_listening_port_remote_by_service(server_ip, server_user,
                                                   server_pwd, service,
                                                   port, listen_addr)

        # start vm and prepare to migrate
        if not vm.is_alive():
            vm.start()

        migrate_vm_with_ipv6(test_dict)

    finally:
        logging.info("Recovery test environment")
        session = remote.wait_for_login('ssh', server_ip, '22',
                                        server_user, server_pwd,
                                        r"[\#\$]\s*$")
        cmd = "virsh destroy %s" % vm_name
        logging.info("Execute %s on %s", cmd, server_ip)
        status, output = session.cmd_status_output(cmd)
        if status:
            session.close()
            raise error.TestError(output)

        session.close()

        logging.info("Recovery VM XML configration")
        vmxml_backup.sync()
        logging.debug("The current VM XML:\n%s", vmxml_backup.xmltreefile)

        logging.info("Recover virt NFS SELinux boolean")
        # keep .ssh/authorized_keys for NFS cleanup later
        se_obj.cleanup(True)

        logging.info("Cleanup NFS test environment...")
        nfs_serv.unexportfs_in_clean = True
        nfs_serv.cleanup()
        nfs_cli.cleanup()

        cleanup(objs_list)
