import logging

from avocado.utils import process

from virttest import remote
from virttest import ssh_key
from virttest import virsh
from virttest import libvirt_vm
from virttest.libvirt_xml import capability_xml
from virttest.utils_test import libvirt as utlv


def run(test, params, env):
    """
    Test command virsh cpu-models
    """
    cpu_arch = params.get("cpu_arch", "")
    option = params.get("option", "")
    target_uri = params.get("target_uri", "default")
    status_error = "yes" == params.get("status_error", "no")
    restart_libvirtd_remotely = "yes" == params.get(
        "restart_libvirtd_remotely", "no")

    remote_ip = params.get("remote_ip", "REMOTE.EXAMPLE.COM")
    remote_pwd = params.get("remote_pwd", None)

    local_ip = params.get("local_ip", "LOCAL.EXAMPLE.COM")
    local_pwd = params.get("local_pwd", None)

    if 'EXAMPLE.COM' in (target_uri, remote_ip, local_ip):
        test.error("Please replace '%s/%s/%s' with valid uri or remote_ip or "
                   "local_ip" % (target_uri, remote_ip, local_ip))

    if restart_libvirtd_remotely:
        try:
            session = remote.remote_login("ssh", remote_ip, "22", "root",
                                          remote_pwd, "#")
            session.cmd_output('LANG=C')
            ssh_key.setup_remote_ssh_key(remote_ip, "root", remote_pwd,
                                         local_ip, "root", local_pwd)

            cmd = "service libvirtd restart"
            status, output = session.cmd_status_output(cmd, internal_timeout=5)
            logging.debug("cmd: %s, status: %s, output: %s"
                          % (cmd, status, output))
        except process.CmdError, info:
            test.error("Fail to restart libvirtd on remote: %s" % info)
        finally:
            session.close()

    connect_uri = libvirt_vm.normalize_connect_uri(target_uri)
    arch_list = []
    if not cpu_arch:
        try:
            capa = capability_xml.CapabilityXML()
            guest_map = capa.get_guest_capabilities()
            guest_arch = []
            for v in guest_map.values():
                guest_arch += v.keys()
            for arch in set(guest_arch):
                arch_list.append(arch)
        except Exception, e:
            test.error("Fail to get guest arch list of the host"
                       " supported:\n%s" % e)
    else:
        arch_list.append(cpu_arch)
    for arch in arch_list:
        logging.debug("Get the CPU models for arch: %s" % arch)
        result = virsh.cpu_models(arch, options=option, uri=connect_uri,
                                  ignore_status=True, debug=True)
        utlv.check_exit_status(result, expect_error=status_error)
