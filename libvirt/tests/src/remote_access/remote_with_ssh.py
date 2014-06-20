import logging
import commands
from virttest import remote
from virttest import aexpect
from virttest.utils_net import set_net_if_ip, del_net_if_ip
from autotest.client.shared import error


def compare_virt_version(params):
    """
    Make sure libvirt version is different
    """
    remote_ip = params.get("remote_ip", "REMOTE.EXAMPLE.COM")
    remote_user = params.get("remote_user")
    remote_pwd = params.get("remote_pwd")
    transport = params.get("transport")
    port = params.get("port")
    query_cmd = params.get("query_cmd", "rpm -q libvirt")
    # query libvirt version on local host
    status, output_local = commands.getstatusoutput(query_cmd)
    if status:
        raise error.TestError(output_local)
    # query libvirt version on remote host
    session = remote.wait_for_login(transport, remote_ip, port,
                                    remote_user, remote_pwd, "#")
    status, output_remote = session.cmd_status_output(query_cmd)
    if status:
        raise error.TestError(output_remote)
    # compare libvirt version between local and remote host
    if output_local == output_remote.strip():
        raise error.TestError("To expect different libvirt version "
                              "<%s>:<%s>", output_local, output_remote)


def setup_clean_ipv6(params):
    """
    Configure and clean up IPv6 environment
    """
    remote_ip = params.get("remote_ip", "REMOTE.EXAMPLE.COM")
    remote_user = params.get("remote_user")
    remote_pwd = params.get("remote_pwd")
    transport = params.get("transport")
    port = params.get("port")
    client_iface = params.get("client_iface", "eth0")
    server_iface = params.get("server_iface", "eth0")
    client_ipv6_addr = params.get("client_ipv6_addr")
    server_ipv6_addr = params.get("server_ipv6_addr")
    auto_clean = params.get("auto_clean", "no")

    if auto_clean == "no":
        logging.info("Prepare to configure IPv6 test environment...")
        # configure global IPv6 address for local host
        set_net_if_ip(client_iface, client_ipv6_addr)
        # configure global IPv6 address for remote host
        session = remote.wait_for_login(transport, remote_ip, port,
                                        remote_user, remote_pwd, "#")
        runner = remote.RemoteRunner(session=session).run
        set_net_if_ip(server_iface, server_ipv6_addr, runner)
    else:
        logging.info("Prepare to clean up IPv6 test environment...")
        # delete global IPv6 address from local host
        del_net_if_ip(client_iface, client_ipv6_addr)
        # delete global IPv6 address from remote host
        session = remote.wait_for_login(transport, remote_ip, port,
                                        remote_user, remote_pwd, "#")
        runner = remote.RemoteRunner(session=session).run
        del_net_if_ip(server_iface, server_ipv6_addr, runner)


def connect_libvirtd(params):
    """
    Connect libvirt daemon service
    """
    server_ip = params.get("server_ip")
    remote_ip = params.get("remote_ip", "REMOTE.EXAMPLE.COM")
    remote_pwd = params.get("remote_pwd")
    transport = params.get("transport")
    status_error = params.get("status_error")
    uri_path = params.get("uri_path")
    virsh_cmd = params.get("virsh_cmd", "list")
    read_only = params.get("read_only", "")
    vm_name = params.get("main_vm")
    config_ipv6 = params.get("config_ipv6")
    uri = ""

    if server_ip:
        uri = "qemu+%s://%s/%s" % (transport, server_ip, uri_path)
    else:
        uri = "qemu+%s://%s/%s" % (transport, remote_ip, uri_path)

    command = "virsh %s -c %s %s %s" % (read_only, uri, virsh_cmd, vm_name)
    logging.info("Execute %s", command)
    session = aexpect.ShellSession(command, echo=True)
    patterns_password = [r".*password:\s*"]
    patterns_virsh_list = [r".*Id\s*Name\s*State\s*.*"]

    # if the error is an expected or uri is empty string or w/o
    # any IPv6 configuration then expect returning error prompt
    if status_error == "yes" or not uri_path or config_ipv6 == "no":
        patterns_virsh_list = [r".*[Ee]rror.*"]
    # expect inputting a password
    if (transport == "ssh" and status_error == "no"
            or not uri_path or read_only != ""):
        match, text = session.read_until_any_line_matches(patterns_password,
                                                          timeout=10,
                                                          internal_timeout=1)
        if match == -1:
            logging.info("The text %s is an expected result", text)
            session.sendline(remote_pwd)
        else:
            logging.debug("Prompt text: %s", text)
            return False
    # execute 'virsh list' then expect a successful or failed result
    match, text = session.read_until_any_line_matches(patterns_virsh_list,
                                                      timeout=10,
                                                      internal_timeout=1)
    if match == -1:
        logging.info("The text %s is an expected result", text)
    else:
        logging.debug("Prompt text: %s", text)
        return False

    session.close()
    return True


def run(test, params, env):
    """
    Test remote access via SSH transport
    """
    config_ipv6 = params.get("config_ipv6", "no")
    diff_virt_ver = params.get("diff_virt_ver", "no")

    status_error = params.get("status_error")
    test_dicts = dict(params)

    try:
        if config_ipv6 == "yes":
            setup_clean_ipv6(test_dicts)
            test_dicts["auto_clean"] = "yes"

        if diff_virt_ver == "yes":
            compare_virt_version(test_dicts)

        if status_error == "no":
            if not connect_libvirtd(test_dicts):
                raise error.TestFail("Failed to connect libvirt daemon!!")
            else:
                logging.info("Succeed to connect libvirt daemon.")
        else:
            if connect_libvirtd(test_dicts):
                logging.info("It's an expected error!!")
            else:
                raise error.TestFail("Unexpected return status")
    finally:
        if test_dicts.get("auto_clean") == "yes":
            setup_clean_ipv6(test_dicts)
