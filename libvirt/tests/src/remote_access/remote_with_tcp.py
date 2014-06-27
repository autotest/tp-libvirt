import re
import logging
import commands

from autotest.client import os_dep
from autotest.client.shared import error
from virttest import remote
from virttest import aexpect
from virttest.utils_conn import TCPConnection
from virttest.utils_sasl import SASL
from virttest.utils_net import set_net_if_ip, del_net_if_ip, get_net_if_addrs


_VIRT_SECRETS_PATH = "/etc/libvirt/secrets"


def check_ipv6_connectivity(params):
    """
    Check IPv6 network connectivity
    """
    ipv6_addr_des = params.get("ipv6_addr_des")
    client_iface = params.get("client_iface", "eth0")
    try:
        os_dep.command("ping6")
    except ValueError:
        raise error.TestNAError("Can't find ping6 command")
    ping6_cmd = "ping6 -I %s %s -c %s" % (client_iface, ipv6_addr_des, 10)
    status, out = commands.getstatusoutput(ping6_cmd)
    if status:
        raise error.TestNAError("The <%s> destination is "
                                "unreachable: %s", ipv6_addr_des, out)
    else:
        logging.info("The <%s> destination is connectivity!", ipv6_addr_des)


def check_tcp_ip_listening(params):
    """
    Check TCP/IP connections listening
    """
    remote_ip = params.get("server_ip", "REMOTE.EXAMPLE.COM")
    remote_user = params.get("server_user")
    remote_pwd = params.get("server_pwd")
    client = params.get("client")
    port = params.get("port")
    tcp_port = params.get("tcp_port", "16509")
    listen_addr = params.get("listen_addr")
    # setup remote session
    session = remote.wait_for_login(client, remote_ip, port,
                                    remote_user, remote_pwd, "#")
    # check if ip6tables command exists
    if session.cmd_status("which netstat"):
        raise error.TestNAError("Can't find netstat command")
    # check tcp listening
    listen_addr = listen_addr + ":" + tcp_port

    out = session.cmd_output("netstat -tunlp | grep libvirtd").strip()
    if not re.search(listen_addr, out, re.M):
        raise error.TestFail("Failed to listen TCP/IP connections: %s", out)
    logging.info("The listening state: %s", out)


def setup_clean_ipv6(params):
    """
    Configure and clean up IPv6 environment
    """
    remote_ip = params.get("server_ip", "REMOTE.EXAMPLE.COM")
    remote_user = params.get("server_user")
    remote_pwd = params.get("server_pwd")
    client = params.get("client")
    port = params.get("port")
    client_iface = params.get("client_iface", "eth0")
    server_iface = params.get("server_iface", "eth0")
    client_ipv6_addr = params.get("client_ipv6_addr")
    server_ipv6_addr = params.get("server_ipv6_addr")
    ipv6_addr_src = params.get("ipv6_addr_src")
    ipv6_addr_des = params.get("ipv6_addr_des")
    clean_ipv6 = params.get("clean_ipv6", "no")

    try:
        # setup remote session and runner
        session = remote.wait_for_login(client, remote_ip, port,
                                        remote_user, remote_pwd, "#")
        runner = session.cmd_output

        if clean_ipv6 == "no":
            logging.info("Prepare to configure IPv6 test environment...")
            # configure global IPv6 address for local host
            set_net_if_ip(client_iface, client_ipv6_addr)
            # configure global IPv6 address for remote host
            set_net_if_ip(server_iface, server_ipv6_addr, runner)
            # check IPv6 network connectivity
            check_ipv6_connectivity(params)
        else:
            logging.info("Prepare to clean up IPv6 test environment...")
            # delete global IPv6 address from local host
            local_ipv6_addr_list = get_net_if_addrs(client_iface).get("ipv6")
            logging.debug("Local IPv6 address list: %s", local_ipv6_addr_list)
            if ipv6_addr_src in local_ipv6_addr_list:
                del_net_if_ip(client_iface, client_ipv6_addr)
            # delete global IPv6 address from remote host
            remote_ipv6_addr_list = get_net_if_addrs(server_iface,
                                                     runner).get("ipv6")
            logging.debug("remote IPv6 address list: %s", remote_ipv6_addr_list)
            if ipv6_addr_des in remote_ipv6_addr_list:
                del_net_if_ip(server_iface, server_ipv6_addr, runner)
    except:
        session.close()
        raise


def connect_libvirtd(params):
    """
    Connect libvirt daemon
    """
    status_error = params.get("status_error", "no")
    uri = params.get("uri")
    auth_name = params.get("sasl_user")
    auth_pwd = params.get("sasl_pwd")
    virsh_cmd = params.get("virsh_cmd", "list")
    read_only = params.get("read_only", "")
    vm_name = params.get("main_vm")
    patterns_auth_name = [r".*[Pp]lease.*authentication name:\s*$"]
    patterns_auth_pwd = [r".*[Pp]assword:\s*$"]
    patterns_virsh_list = [r".*Id\s*Name\s*State\s*.*"]

    # if the error is an expected then 'virsh list' will return error
    if status_error == "yes":
        patterns_virsh_list = [r".*[Ee]rror.*"]

    command = "virsh %s -c %s %s %s" % (read_only, uri, virsh_cmd, vm_name)
    logging.info("Execute %s", command)
    # setup shell session
    session = aexpect.ShellSession(command, echo=True)

    try:
        # requires access authentication
        if params.get("sasl_user_pwd"):
            match, txt = session.read_until_any_line_matches(patterns_auth_name,
                                                             timeout=10,
                                                             internal_timeout=1)
            if match == -1:
                logging.info("The text %s is an expected result", txt)
                session.sendline(auth_name)
            else:
                logging.debug("Prompt text(auth_name): %s", txt)
                return False

            match, txt = session.read_until_any_line_matches(patterns_auth_pwd,
                                                             timeout=10,
                                                             internal_timeout=1)
            if match == -1:
                logging.info("The text %s is an expected result", txt)
                session.sendline(auth_pwd)
            else:
                logging.debug("Prompt text(auth_pwd): %s", txt)
                return False

        # execute 'virsh' command then expect a successful or failed result
        match, text = session.read_until_any_line_matches(patterns_virsh_list,
                                                          timeout=10,
                                                          internal_timeout=1)
        if match == -1:
            logging.info("The text %s is an expected result", text)
        else:
            logging.debug("Prompt text(virsh %s): %s", virsh_cmd, text)
            return False
    except (aexpect.ShellError, aexpect.ExpectError), details:
        log = session.get_output()
        session.close()
        raise error.TestFail("Failed to connect libvirtd: %s\n%s"
                             % (details, log))
    return True


def try_to_connect_libvirtd(params):
    """
    Try to connect libvirt daemon
    """
    status_error = params.get("status_error", "no")
    try:
        if status_error == "no":
            if not connect_libvirtd(params):
                raise error.TestFail("Failed to connect libvirt daemon!!")
            else:
                logging.info("Succeed to connect libvirt daemon.")
        else:
            if connect_libvirtd(params):
                logging.info("It's an expected error!!")
            else:
                raise error.TestFail("Unexpected return result")
    except error.TestFail:
        # recovery test environment
        cleanup(params)


def clean_ip6tables(params):
    """
    Clean up IPv6 iptables on the remote host, the default
    ip6tables is reject-with icmp6-adm-prohibited
    """
    remote_ip = params.get("server_ip", "REMOTE.EXAMPLE.COM")
    remote_user = params.get("server_user")
    remote_pwd = params.get("server_pwd")
    client = params.get("client")
    port = params.get("port")

    # setup remote session
    session = remote.wait_for_login(client, remote_ip, port,
                                    remote_user, remote_pwd, "#")
    # check if ip6tables command exists
    if session.cmd_status("which ip6tables"):
        raise error.TestNAError("Can't find ip6tables command")
    # flush ip6tables rules
    if session.cmd_status("ip6tables -F"):
        raise error.TestFail("Failed to flush 'icmp6-adm-prohibited' rule")


def cleanup(params):
    """
    Clean up test environment.
    """
    # recovery test environment
    sasl_obj = params.get("sasl_obj")
    tcp_obj = params.get("tcp_obj")

    if params.get("clean_ipv6") == "yes":
        setup_clean_ipv6(params)
    if sasl_obj:
        sasl_obj.cleanup()
    if tcp_obj:
        tcp_obj.conn_recover()


def run(test, params, env):
    """
    Test remote access with TCP connection
    """

    test_dict = dict(params)
    config_ipv6 = test_dict.get("config_ipv6", "no")
    tcp_port = test_dict.get("tcp_port", "16509")
    remote_ip = test_dict.get("remote_ip_addr")
    server_ip = test_dict.get("server_ip")
    listen_addr = test_dict.get("listen_addr")
    no_any_config = test_dict.get("no_any_config", "no")
    sasl_user_pwd = test_dict.get("sasl_user_pwd")
    sasl_allowed_users = test_dict.get("sasl_allowed_users")
    test_dict["clean_ipv6"] = "no"

    # only simply connect libvirt daemon then return
    if no_any_config == "yes":
        test_dict["uri"] = "qemu+tcp://system"
        try_to_connect_libvirtd(test_dict)
        return

    # construct virsh URI
    if remote_ip:
        test_dict["uri"] = "qemu+tcp://%s:%s/system" % (remote_ip, tcp_port)
    else:
        test_dict["uri"] = "qemu+tcp://%s:%s/system" % (server_ip, tcp_port)

    try:
        if config_ipv6 == "yes":
            setup_clean_ipv6(test_dict)
            clean_ip6tables(test_dict)
            test_dict["clean_ipv6"] = "yes"

        tcp_obj = TCPConnection(test_dict)
        # setup test environment
        tcp_obj.conn_setup()
        test_dict["tcp_obj"] = tcp_obj
        # check TCP/IP listening
        if listen_addr:
            check_tcp_ip_listening(test_dict)

        if sasl_user_pwd and sasl_allowed_users:
            # covert string tuple and list to python data type
            sasl_user_pwd = eval(sasl_user_pwd)
            sasl_allowed_users = eval(sasl_allowed_users)

            # create a sasl user
            sasl_obj = SASL(test_dict)
            sasl_obj.setup()
            test_dict["sasl_obj"] = sasl_obj

            for sasl_user, sasl_pwd in sasl_user_pwd:
                test_dict["sasl_user"] = sasl_user
                test_dict["sasl_pwd"] = sasl_pwd

                if sasl_user not in sasl_allowed_users:
                    test_dict["status_error"] = "yes"

                try_to_connect_libvirtd(test_dict)
        else:
            try_to_connect_libvirtd(test_dict)

    finally:
        # recovery test environment
        cleanup(test_dict)
