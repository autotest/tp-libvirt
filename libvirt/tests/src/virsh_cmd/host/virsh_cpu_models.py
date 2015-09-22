import logging

from autotest.client.shared import error

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
    logging.debug(target_uri.count('EXAMPLE.COM'))
    if target_uri.count('EXAMPLE.COM'):
        raise error.TestNAError("Please replace '%s' with valid uri" %
                                target_uri)
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
            raise error.TestError("Fail to get guest arch list of the"
                                  " host supported:\n%s" % e)
    else:
        arch_list.append(cpu_arch)
    for arch in arch_list:
        logging.debug("Get the CPU models for arch: %s" % arch)
        result = virsh.cpu_models(arch, options=option, uri=connect_uri,
                                  ignore_status=True, debug=True)
        utlv.check_exit_status(result, expect_error=status_error)
