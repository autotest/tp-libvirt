import logging
import re
import os

from virttest import data_dir, error_context, remote, utils_misc
from avocado.core import exceptions
from avocado.utils import process

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
    Compile netperf source package

    :param params: Test parameters dictionary
    :param env: Test environment object
    :param address: localhost, vm name, or ip address
    :return: netserver_path, netperf_path
    """
    netperf_link = params.get("netperf_link", "netperf-2.7.1.tar.bz2")
    netperf_src = os.path.join(data_dir.get_deps_dir("netperf"), netperf_link)
    guest_netperf_path = params.get("guest_netperf_path", "/var/tmp/")

    if address == "localhost":
        session, install_path = None, params.get("server_path", "/var/tmp")
    elif address in params.get('vms', '').split():
        vm = env.get_vm(address)
        session, install_path = vm.wait_for_login(), guest_netperf_path
        target_ip, user, pwd = vm.get_address(), params.get("username"), params.get("password")
    elif re.match(r"^(?:\d{1,3}\.){3}\d{1,3}$", address):
        if params.get_boolean("remote_server", False):
            session = remote.remote_login(
                          "ssh",
                          address,
                          "22",
                          params.get("server_user"),
                          params.get("server_pwd"),
                          r'[$#%]'
                      )
        else:
            raise exceptions.TestError(
                f"IP address '{address}' provided but 'remote_server' param is not set. "
                "Set remote_server=yes to compile on remote server, or use 'localhost'."
            )
        install_path = params.get("server_path", "/var/tmp")
        target_ip, user, pwd = address, params.get("server_user"), params.get("server_pwd")
    else:
        raise exceptions.TestError(f"Unsupported address for compilation: {address}")

    if netperf_link.endswith(".tar.bz2"):
        pack_suffix, decomp_tool = ".tar.bz2", "tar jxf"
    elif netperf_link.endswith(".tar.gz"):
        pack_suffix, decomp_tool = ".tar.gz", "tar zxf"
    else:
        raise exceptions.TestError(f"Unsupported compression format for netperf package: {netperf_link}")

    full_netperf_dir = os.path.join(install_path, netperf_link[:-len(pack_suffix)])
    nserver_path = os.path.join(full_netperf_dir, "src", "netserver")
    nperf_path = os.path.join(full_netperf_dir, "src", "netperf")

    try:
        if session:
            if session.cmd_status(f"test -f {nperf_path}") == 0:
                LOG_JOB.info(f"Netperf already compiled on {address}, skipping.")
                return nserver_path, nperf_path
        elif os.path.exists(nperf_path):
            LOG_JOB.info("Netperf already compiled on localhost, skipping.")
            return nserver_path, nperf_path

        if session:
            arch = session.cmd_output("arch", timeout=10).strip()
            decomp_cmd = f"cd {install_path} && {decomp_tool} {netperf_link}"
        else:
            arch = process.run("arch").stdout_text.strip()
            decomp_cmd = f"{decomp_tool} {netperf_src} -C {install_path}"

        build_type_map = {
            "aarch64": "aarch64-unknown-linux-gnu",
            "x86_64": "x86_64-unknown-linux-gnu",
        }
        build_type = build_type_map.get(arch, arch)
        compile_cmd = (f"cd {full_netperf_dir} && ./autogen.sh && "
                       f"CFLAGS='-Wno-implicit-function-declaration' ./configure "
                       f"--build={build_type} --prefix={install_path} && make")

        LOG_JOB.info(f"Compiling netperf from source on {address}...")
        if session:
            remote.copy_files_to(
                target_ip,
                params.get("cp_client", "scp"),
                user,
                pwd,
                params.get("cp_port", "22"),
                netperf_src,
                install_path,
                600
            )
            session.cmd(decomp_cmd, timeout=60)
            session.cmd(compile_cmd, timeout=600)
        else:
            process.run(decomp_cmd, shell=True, timeout=60)
            process.run(compile_cmd, shell=True, timeout=600)

        LOG_JOB.info(f"Netperf compilation completed on {address}")
    finally:
        if session:
            session.close()
    return nserver_path, nperf_path
