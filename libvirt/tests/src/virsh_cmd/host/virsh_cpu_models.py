import logging
from six import itervalues

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
    status_error = "yes" == params.get("status_error", "no")
    remote_ref = params.get("remote_ref", "")
    connect_uri = libvirt_vm.normalize_connect_uri(params.get("connect_uri",
                                                              "default"))

    if remote_ref == "remote":
        remote_ip = params.get("remote_ip", "REMOTE.EXAMPLE.COM")
        remote_pwd = params.get("remote_pwd", None)

        if 'EXAMPLE.COM' in remote_ip:
            test.cancel("Please replace '%s' with valid remote ip" % remote_ip)

        ssh_key.setup_ssh_key(remote_ip, "root", remote_pwd)
        connect_uri = libvirt_vm.complete_uri(remote_ip)

    arch_list = []
    if not cpu_arch:
        try:
            capa = capability_xml.CapabilityXML()
            guest_map = capa.get_guest_capabilities()
            guest_arch = []
            for v in list(itervalues(guest_map)):
                guest_arch += list(v.keys())
            for arch in set(guest_arch):
                arch_list.append(arch)
        except Exception as e:
            test.error("Fail to get guest arch list of the host"
                       " supported:\n%s" % e)
    else:
        arch_list.append(cpu_arch)
    for arch in arch_list:
        logging.debug("Get the CPU models for arch: %s" % arch)
        result = virsh.cpu_models(arch, options=option, uri=connect_uri,
                                  ignore_status=True, debug=True)
        utlv.check_exit_status(result, expect_error=status_error)
