import logging
import os

from virttest import data_dir, error_context, remote, utils_misc, utils_net, utils_netperf

LOG_JOB = logging.getLogger("avocado.test")


@error_context.context_aware
def record_env_version(test, params, host, server_ctl, fd, test_duration):
    """
    Get host kernel/qemu/guest kernel version

    """
    ver_cmd = params.get("ver_cmd", "rpm -q qemu-kvm")
    guest_ver_cmd = params.get("guest_ver_cmd", "uname -r")
    libvirt_ver_cmd = params.get("libvirt_ver_cmd", "rpm -q libvirt")

    test.write_test_keyval({"kvm-userspace-ver": ssh_cmd(host, ver_cmd).strip()})
    test.write_test_keyval(
        {"guest-kernel-ver": ssh_cmd(server_ctl, guest_ver_cmd).strip()}
    )
    test.write_test_keyval({"libvirt-ver": ssh_cmd(host, libvirt_ver_cmd).strip()})
    test.write_test_keyval({"session-length": test_duration})
    fd.write("### kvm-userspace-ver : %s\n" % ssh_cmd(host, ver_cmd).strip())
    fd.write("### guest-kernel-ver : %s\n" % ssh_cmd(server_ctl, guest_ver_cmd).strip())
    fd.write("### libvirt-ver : %s\n" % ssh_cmd(host, libvirt_ver_cmd).strip())
    fd.write("### kvm_version : %s\n" % os.uname()[2])
    fd.write("### session-length : %s\n" % test_duration)


def env_setup(test, params, session, ip, username, shell_port, password):
    """
    Prepare the test environment in server/client/host

    """
    error_context.context("Setup env for %s" % ip)
    if params.get("env_setup_cmd"):
        ssh_cmd(session, params.get("env_setup_cmd"), ignore_status=True)

    pkg = params["netperf_pkg"]
    pkg = os.path.join(data_dir.get_deps_dir(), pkg)
    remote.scp_to_remote(ip, shell_port, username, password, pkg, "/tmp")
    ssh_cmd(session, params.get("setup_cmd"))

    agent_path = os.path.join(test.virtdir, "scripts/netperf_agent.py")
    remote.scp_to_remote(ip, shell_port, username, password, agent_path, "/tmp")


def tweak_tuned_profile(params, server_ctl, client, host):
    """

    Tweak configuration with truned profile

    """

    client_tuned_profile = params.get("client_tuned_profile")
    server_tuned_profile = params.get("server_tuned_profile")
    host_tuned_profile = params.get("host_tuned_profile")
    error_context.context("Changing tune profile of guest", LOG_JOB.info)
    if server_tuned_profile:
        ssh_cmd(server_ctl, server_tuned_profile)

    error_context.context("Changing tune profile of client/host", LOG_JOB.info)
    if client_tuned_profile:
        ssh_cmd(client, client_tuned_profile)
    if host_tuned_profile:
        ssh_cmd(host, host_tuned_profile)


def ssh_cmd(session, cmd, timeout=120, ignore_status=False):
    """
    Execute remote command and return the output

    :param session: a remote shell session or tag for localhost
    :param cmd: executed command
    :param timeout: timeout for the command
    :param ignore_status: whether to ignore command exit status
    :return: command stdout
    """
    kwargs = {"timeout": timeout, "ignore_status": ignore_status}
    if session == "localhost":
        kwargs["shell"] = True
    else:
        kwargs["session"] = session
    _, output = utils_misc.cmd_status_output(cmd, **kwargs)
    return output


def netperf_thread(params, numa_enable, client_s, option, fname):
    """
    Start netperf thread on client

    """
    cmd = ""
    if numa_enable and params.get("numa_node") is not None:
        n = abs(int(params.get("numa_node"))) - 1
        cmd += "numactl --cpunodebind=%s --membind=%s " % (n, n)
    cmd += option
    cmd += " >> %s" % fname
    LOG_JOB.info("Start netperf thread by cmd '%s'", cmd)
    ssh_cmd(client_s, cmd)


def format_result(result, base="17", fbase="2"):
    """
    Format the result to a fixed length string.

    :param result: result need to convert
    :param base: the length of converted string
    :param fbase: the decimal digit for float
    """
    if isinstance(result, str):
        value = "%" + base + "s"
    elif isinstance(result, int):
        value = "%" + base + "d"
    elif isinstance(result, float):
        value = "%" + base + "." + fbase + "f"
    else:
        raise TypeError(f"unexpected result type: {type(result).__name__}")
    return value % result


def netperf_record(results, filter_list, header=False, base="17", fbase="2"):
    """
    Record the results in a certain format.

    :param results: a dict include the results for the variables
    :param filter_list: variable list which is wanted to be shown in the
                        record file, /also fix the order of variables
    :param header: if record the variables as a column name before the results
    :param base: the length of a variable
    :param fbase: the decimal digit for float
    """
    key_list = []
    for key in filter_list:
        if key in results:
            key_list.append(key)

    record = ""
    if header:
        for key in key_list:
            record += "%s|" % format_result(key, base=base, fbase=fbase)
        record = record.rstrip("|")
        record += "\n"
    for key in key_list:
        record += "%s|" % format_result(results[key], base=base, fbase=fbase)
    record = record.rstrip("|")
    return record, key_list


def compile_netperf_pkg(params, env, address):
    """
    Prepare and compile netperf binaries on the target system

    :param params: Test parameters dictionary configs
    :param env: Test environment object
    :param address: localhost, vm name, or ip address
    :return: netserver_path, netperf_path
    """
    if address in ("localhost", "127.0.0.1",):
        target_ip = utils_net.get_host_ip_address(params)
        install_path = params.get("server_path", "/var/tmp")
        user = params.get("hostusername", "root")
        pwd = params.get("hostpassword", "")
    elif address in params.get('vms', '').split():
        vm = env.get_vm(address)
        vm.verify_alive()
        target_ip = vm.get_address()
        install_path = params.get("client_path", "/var/tmp")
        user = params.get("username", "")
        pwd = params.get("password", "")
    else:
        target_ip = address
        install_path = params.get("server_path", "/var/tmp")
        user = params.get("remote_username", "")
        pwd = params.get("remote_password", "")

    netperf_link = params.get("netperf_link")
    netperf_src = os.path.join(data_dir.get_deps_dir("netperf"), netperf_link)

    LOG_JOB.info(f"Instantiating NetperfServer on {address} (IP: {target_ip})...")
    n_server = utils_netperf.NetperfServer(
        address=target_ip,
        netperf_path=install_path,
        md5sum=params.get("pkg_md5sum", ""),
        netperf_source=netperf_src,
        username=user,
        password=pwd,
        compile_option="--enable-demo=yes",
        install=True
    )

    nserver_path = n_server.netserver_path
    nperf_path = n_server.netperf_path

    if n_server.session:
        n_server.session.close()
    if n_server.package and hasattr(n_server.package, "_release_session"):
        n_server.package._release_session()

    return nserver_path, nperf_path
